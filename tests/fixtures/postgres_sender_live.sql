-- Prepares the `postgres` database of a throwaway Supabase-image server for
-- tests/unit/test_postgres_sender_live.py. One proof connects as a superuser
-- owner (as on a self-managed PostgreSQL); the other as `postgres`, a
-- non-superuser CREATEROLE owner (as on Supabase). Disposable servers only.
--
-- pg_net's worker serves only the `postgres` database. The sender refuses
-- pg_net while PUBLIC or any non-owner can use it, so the image's shared grants
-- go; a dedicated writer may only insert source rows.

CREATE EXTENSION IF NOT EXISTS pg_net VERSION '0.20.4';

REVOKE ALL ON SCHEMA net FROM PUBLIC, supabase_functions_admin, anon, authenticated, service_role;
REVOKE ALL ON ALL TABLES IN SCHEMA net FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA net FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA net FROM PUBLIC;

-- A non-superuser can grant the sender pg_net access, and pass the queue
-- preflight, only as pg_net's owner: the preparing superuser hands it over.
ALTER SCHEMA net OWNER TO postgres;
ALTER TABLE net.http_request_queue OWNER TO postgres;
ALTER TABLE net._http_response OWNER TO postgres;
DO $owner$
DECLARE
  routine regprocedure;
BEGIN
  FOR routine IN SELECT oid FROM pg_catalog.pg_proc WHERE pronamespace = 'net'::regnamespace LOOP
    EXECUTE pg_catalog.format('ALTER ROUTINE %s OWNER TO postgres', routine);
  END LOOP;
END
$owner$;

CREATE TABLE public.tr2_source_rows (id integer PRIMARY KEY, status text NOT NULL, note text NOT NULL);
CREATE TABLE public.tr2_other_rows (id integer PRIMARY KEY, status text NOT NULL, note text NOT NULL);
ALTER TABLE public.tr2_source_rows OWNER TO postgres;
ALTER TABLE public.tr2_other_rows OWNER TO postgres;
CREATE ROLE tr2_source_writer NOLOGIN;
GRANT INSERT ON public.tr2_source_rows, public.tr2_other_rows TO tr2_source_writer;
GRANT tr2_source_writer TO postgres;
