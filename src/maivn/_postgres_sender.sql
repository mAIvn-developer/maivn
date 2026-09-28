-- Source-owned SQL template for maivn.postgres.postgres_sender_plan.
-- No connection secret appears in this resource or the rendered plan.
DO $preflight$
BEGIN
  IF pg_catalog.to_regclass('brain.sessions') IS NOT NULL
     AND pg_catalog.to_regclass('integrations.external_connections') IS NOT NULL THEN
    RAISE EXCEPTION 'Use your own database, not the mAIvn platform database';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'pg_net' AND extversion = '0.20.4') THEN
    RAISE EXCEPTION 'This sender requires reviewed pg_net 0.20.4; ask your database owner to prepare it';
  END IF;
  IF pg_catalog.to_regprocedure('extensions.hmac(bytea,bytea,text)') IS NULL THEN
    RAISE EXCEPTION 'This sender requires pgcrypto in the extensions schema';
  END IF;
  IF pg_catalog.to_regclass('"__SOURCE_SCHEMA__"."__SOURCE_TABLE__"') IS NULL THEN
    RAISE EXCEPTION 'The selected source table does not exist';
  END IF;
  IF (SELECT relkind FROM pg_catalog.pg_class WHERE oid = '"__SOURCE_SCHEMA__"."__SOURCE_TABLE__"'::regclass) <> 'r' THEN
    RAISE EXCEPTION 'This sender supports an ordinary table, not a view or partitioned parent';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.unnest(ARRAY[__APPROVED_COLUMNS__]::text[]) chosen(column_name)
    WHERE NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_attribute attribute
      WHERE attribute.attrelid = '"__SOURCE_SCHEMA__"."__SOURCE_TABLE__"'::regclass
        AND attribute.attname = chosen.column_name AND attribute.attnum > 0 AND NOT attribute.attisdropped
    )
  ) THEN
    RAISE EXCEPTION 'A selected column does not exist on the source table';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace n,
      LATERAL pg_catalog.aclexplode(coalesce(n.nspacl, pg_catalog.acldefault('n', n.nspowner))) a
    WHERE n.nspname = 'net' AND a.grantee = 0
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace,
      LATERAL pg_catalog.aclexplode(c.relacl) a
    WHERE n.nspname = 'net' AND a.grantee = 0
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace,
      LATERAL pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) a
    WHERE n.nspname = 'net' AND a.grantee = 0
  ) THEN
    RAISE EXCEPTION 'pg_net grants PUBLIC access; your database owner must review shared permissions before installation';
  END IF;
  -- Any non-owner queue reader/writer can see or forge signed deliveries.
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace,
      LATERAL pg_catalog.aclexplode(c.relacl) a
    WHERE n.nspname = 'net' AND c.relname = 'http_request_queue'
      AND a.grantee <> c.relowner
  ) THEN
    RAISE EXCEPTION 'pg_net queue has additional grants; review all queue readers and writers before installation';
  END IF;
  PERFORM net.check_worker_is_up();
END
$preflight$;

-- Dedicated sender role. Do not reuse an existing role or grant its membership.
CREATE ROLE __SENDER_ROLE__ NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Schema and table remain owned by the installing trusted administrator.
CREATE SCHEMA __PRIVATE_SCHEMA__;
REVOKE ALL ON SCHEMA __PRIVATE_SCHEMA__ FROM PUBLIC;
GRANT USAGE ON SCHEMA __PRIVATE_SCHEMA__ TO __SENDER_ROLE__;

CREATE TABLE __PRIVATE_SCHEMA__.config (
  singleton boolean PRIMARY KEY CHECK (singleton),
  source_relation oid NOT NULL,
  secret bytea NOT NULL CHECK (octet_length(secret) >= 32)
);
REVOKE ALL ON TABLE __PRIVATE_SCHEMA__.config FROM PUBLIC;
GRANT SELECT ON TABLE __PRIVATE_SCHEMA__.config TO __SENDER_ROLE__;
-- No RLS policy changes to the source table; this private table uses ACLs.
-- Verify installing-role default privileges did not grant any other role access.

GRANT USAGE ON SCHEMA extensions, net TO __SENDER_ROLE__;
GRANT EXECUTE ON FUNCTION extensions.hmac(bytea, bytea, text) TO __SENDER_ROLE__;
GRANT EXECUTE ON FUNCTION net.http_post(text, jsonb, jsonb, jsonb, integer)
  TO __SENDER_ROLE__;
-- Version-specific pg_net invoker implementation privileges. Confirm installed
-- function bodies/signatures before approval; do not blindly adapt on apply.
GRANT EXECUTE ON FUNCTION net._urlencode_string(character varying),
  net._encode_url_with_params_array(text, text[]), net.wake() TO __SENDER_ROLE__;
GRANT INSERT (method, url, headers, body, timeout_milliseconds), SELECT (id)
  ON TABLE net.http_request_queue TO __SENDER_ROLE__;
GRANT USAGE ON SEQUENCE net.http_request_queue_id_seq TO __SENDER_ROLE__;
-- Shared net ACLs are never modified; the preflight refuses unsafe access.

CREATE FUNCTION __PRIVATE_SCHEMA__.send_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $sender$
DECLARE
  -- Exact constants, operator-bound before approval. No runtime URL arguments.
  approved_schema CONSTANT text := '__SOURCE_SCHEMA__';
  approved_table CONSTANT text := '__SOURCE_TABLE__';
  approved_origin CONSTANT text := '__APPROVED_HTTPS_ORIGIN__';
  approved_connection CONSTANT text := '__CONNECTION_ID__';
  -- Explicit owner-approved projection; never silently send new columns.
  approved_columns CONSTANT text[] := ARRAY[__APPROVED_COLUMNS__];
  config_row __PRIVATE_SCHEMA__.config%ROWTYPE;
  new_image jsonb := NULL;
  old_image jsonb := NULL;
  document jsonb;
  raw_body bytea;
  request_path text;
  signed_second bigint;
  signature text;
BEGIN
  IF TG_WHEN <> 'AFTER' OR TG_LEVEL <> 'ROW' OR TG_NARGS <> 0
     OR TG_OP NOT IN ('INSERT', 'UPDATE', 'DELETE')
     OR TG_TABLE_SCHEMA <> approved_schema OR TG_TABLE_NAME <> approved_table THEN
    RAISE EXCEPTION 'sender binding mismatch';
  END IF;
  SELECT * INTO STRICT config_row FROM __PRIVATE_SCHEMA__.config WHERE singleton;
  IF TG_RELID <> config_row.source_relation THEN
    RAISE EXCEPTION 'sender binding mismatch';
  END IF;
  IF approved_schema !~ '^[A-Za-z_][A-Za-z0-9_$]{0,62}$'
     OR approved_table !~ '^[A-Za-z_][A-Za-z0-9_$]{0,62}$'
     OR approved_connection !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
     OR approved_origin !~ '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$' THEN
    RAISE EXCEPTION 'sender binding invalid';
  END IF;
  -- The installing owner binds one trusted HTTPS origin, never a source writer.
  IF TG_OP <> 'DELETE' THEN
    new_image := pg_catalog.to_jsonb(NEW);
    IF NOT new_image ?& approved_columns THEN
      RAISE EXCEPTION 'sender column binding mismatch';
    END IF;
    SELECT pg_catalog.jsonb_object_agg(key, value) INTO new_image
    FROM pg_catalog.jsonb_each(new_image) WHERE key = ANY (approved_columns);
  END IF;
  IF TG_OP <> 'INSERT' THEN
    old_image := pg_catalog.to_jsonb(OLD);
    IF NOT old_image ?& approved_columns THEN
      RAISE EXCEPTION 'sender column binding mismatch';
    END IF;
    SELECT pg_catalog.jsonb_object_agg(key, value) INTO old_image
    FROM pg_catalog.jsonb_each(old_image) WHERE key = ANY (approved_columns);
  END IF;
  document := pg_catalog.jsonb_build_object(
    'type', TG_OP, 'schema', approved_schema, 'table', approved_table,
    'record', new_image, 'old_record', old_image,
    -- Signed nonce prevents accidental dedupe of identical writes in one second.
    'delivery_id', pg_catalog.gen_random_uuid()::text
  );
  raw_body := pg_catalog.convert_to(document::text, 'UTF8');
  IF pg_catalog.octet_length(raw_body) > 262144 THEN
    RAISE EXCEPTION 'sender payload exceeds receiver limit';
  END IF;
  request_path := '/v1/connections/' || approved_connection || '/events';
  signed_second := pg_catalog.floor(EXTRACT(epoch FROM pg_catalog.clock_timestamp()))::bigint;
  signature := 't=' || signed_second::text || ',v1=' || pg_catalog.encode(
    extensions.hmac(
      pg_catalog.convert_to('POST.' || request_path || '.' || signed_second::text || '.', 'UTF8')
        || raw_body,
      config_row.secret, 'sha256'
    ), 'hex'
  );
  PERFORM net.http_post(
    url := approved_origin || request_path,
    body := document,
    params := '{}'::jsonb,
    headers := pg_catalog.jsonb_build_object(
      'Content-Type', 'application/json', 'X-Maivn-Signature', signature
    ),
    timeout_milliseconds := 5000
  );
  RETURN NULL; -- AFTER row trigger return value is ignored.
END
$sender$;
REVOKE ALL ON FUNCTION __PRIVATE_SCHEMA__.send_change() FROM PUBLIC;
-- Inspect/remove any installing-role default ACL grants to other roles before
-- committing; app roles must receive no EXECUTE or private schema/table grants.
-- Ownership transfer requires CREATE in the containing schema temporarily.
GRANT CREATE ON SCHEMA __PRIVATE_SCHEMA__ TO __SENDER_ROLE__;
ALTER FUNCTION __PRIVATE_SCHEMA__.send_change() OWNER TO __SENDER_ROLE__;
REVOKE CREATE ON SCHEMA __PRIVATE_SCHEMA__ FROM __SENDER_ROLE__;


-- Refuse installer default privileges that would leak the private material.
DO $private_acl$
DECLARE
  sender oid := '__SENDER_ROLE__'::pg_catalog.regrole::oid;
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace,
      LATERAL pg_catalog.aclexplode(c.relacl) a
    WHERE n.nspname = '__PRIVATE_SCHEMA__' AND a.grantee NOT IN (c.relowner, sender)
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace,
      LATERAL pg_catalog.aclexplode(p.proacl) a
    WHERE n.nspname = '__PRIVATE_SCHEMA__' AND a.grantee <> p.proowner
  ) THEN
    RAISE EXCEPTION 'Installing role default privileges expose the private sender; installation rolled back';
  END IF;
END
$private_acl$;
