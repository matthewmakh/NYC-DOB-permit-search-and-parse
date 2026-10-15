#!/usr/bin/env python3
"""Project, participant, and salesperson-alert support for DOB ingestion.

The permit feeds describe records at different stages of the same job.  This
module keeps those raw records intact while giving the application a stable
project layer above them.  All schema work is idempotent so the importer can
safely call it on every run.
"""

from datetime import datetime, timedelta
from typing import Iterable, Optional

from psycopg2.extras import execute_values


PERMIT_INTELLIGENCE_COLUMNS = (
    ("project_id", "BIGINT"),
    ("project_key", "VARCHAR(140)"),
    ("initial_cost", "NUMERIC(16,2)"),
    ("current_status_date", "TIMESTAMP"),
    ("existing_stories_count", "INTEGER"),
    ("proposed_stories_count", "INTEGER"),
    ("existing_dwelling_units", "INTEGER"),
    ("proposed_dwelling_units", "INTEGER"),
    ("applicant_first_name", "VARCHAR(100)"),
    ("applicant_last_name", "VARCHAR(100)"),
    ("applicant_business_name", "VARCHAR(255)"),
    ("applicant_professional_title", "VARCHAR(50)"),
    ("participant_role", "VARCHAR(50)"),
    ("participant_role_confidence", "NUMERIC(4,3)"),
    ("filing_representative_first_name", "VARCHAR(100)"),
    ("filing_representative_last_name", "VARCHAR(100)"),
    ("filing_representative_business_name", "VARCHAR(255)"),
    ("design_professional_first_name", "VARCHAR(100)"),
    ("design_professional_last_name", "VARCHAR(100)"),
    ("design_professional_business_name", "VARCHAR(255)"),
    ("design_professional_license", "VARCHAR(100)"),
    ("related_job_number", "VARCHAR(50)"),
    ("electrical_service_work", "BOOLEAN"),
    ("electrical_general_wiring", "BOOLEAN"),
    ("electrical_lighting_work", "BOOLEAN"),
    ("electrical_temp_construction_service", "BOOLEAN"),
    ("electrical_temp_light_power", "BOOLEAN"),
    ("electrical_hvac_wiring", "BOOLEAN"),
    ("electrical_boiler_burner_wiring", "BOOLEAN"),
    ("electrical_category_work_list", "TEXT"),
    ("electrical_existing_meters", "INTEGER"),
    ("electrical_new_meters", "INTEGER"),
    ("electrical_total_meters", "INTEGER"),
    ("elevator_device_type", "VARCHAR(100)"),
    ("elevator_work_type", "VARCHAR(100)"),
    ("elevator_building_code", "VARCHAR(20)"),
    ("elevator_total_construction_floors", "INTEGER"),
    ("elevator_review_type_ppn", "BOOLEAN"),
    ("elevator_electrical_permit_number", "VARCHAR(100)"),
)


def build_project_key(api_source: Optional[str], job_number: Optional[str],
                      permit_no: Optional[str] = None) -> Optional[str]:
    """Return a source-qualified job identity shared by filing/permit stages."""
    identity = str(job_number or permit_no or "").strip()
    if not identity:
        return None
    source = (api_source or "").lower()
    if source == "dob_now_electrical":
        # Electrical application/permit stages share their own job number.
        # Keep that namespace separate from DOB NOW Build: the two systems can
        # allocate lookalike numbers, while const_bis_job_number below is only
        # a relationship hint and not proof they are the same project.
        identity = identity.split('-', 1)[0]
        system = "DOBNOW-ELECTRICAL"
    elif source == "dob_now_elevator":
        identity = identity.split('-', 1)[0]
        system = "DOBNOW-ELEVATOR"
    elif source.startswith("dob_now"):
        # B01234567-I1, -P1, and their issued work permits are stages or
        # amendments of job B01234567, not separate sales opportunities.
        identity = identity.split('-', 1)[0]
        system = "DOBNOW"
    else:
        system = "BIS"
    return f"{system}:{identity}"[:140]


def ensure_project_intelligence_schema(conn) -> None:
    """Create the project/alert layer and role-aware participant view."""
    cur = conn.cursor()
    try:
        for column, ddl in PERMIT_INTELLIGENCE_COLUMNS:
            cur.execute(
                f"ALTER TABLE permits ADD COLUMN IF NOT EXISTS {column} {ddl}"
            )

        cur.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id BIGSERIAL PRIMARY KEY,
                project_key VARCHAR(140) NOT NULL UNIQUE,
                source_system VARCHAR(20) NOT NULL,
                job_number VARCHAR(50),
                bbl VARCHAR(10),
                bin VARCHAR(50),
                address TEXT,
                borough VARCHAR(20),
                job_type VARCHAR(500),
                work_description TEXT,
                initial_cost NUMERIC(16,2),
                existing_stories_count INTEGER,
                proposed_stories_count INTEGER,
                existing_dwelling_units INTEGER,
                proposed_dwelling_units INTEGER,
                first_filing_date DATE,
                latest_issue_date DATE,
                current_status VARCHAR(100),
                current_status_date TIMESTAMP,
                owner_business_name VARCHAR(255),
                applicant_business_name VARCHAR(255),
                applicant_person_name VARCHAR(225),
                applicant_professional_title VARCHAR(50),
                filing_representative_business_name VARCHAR(255),
                design_professional_business_name VARCHAR(255),
                design_professional_person_name VARCHAR(225),
                design_professional_license VARCHAR(100),
                has_electrical_filing BOOLEAN NOT NULL DEFAULT FALSE,
                electrical_service_work BOOLEAN NOT NULL DEFAULT FALSE,
                electrical_general_wiring BOOLEAN NOT NULL DEFAULT FALSE,
                electrical_lighting_work BOOLEAN NOT NULL DEFAULT FALSE,
                electrical_temp_power BOOLEAN NOT NULL DEFAULT FALSE,
                electrical_hvac_or_boiler_wiring BOOLEAN NOT NULL DEFAULT FALSE,
                electrical_new_meters INTEGER,
                electrical_detail_count INTEGER NOT NULL DEFAULT 0,
                electrical_scope_categories TEXT,
                electrical_floor_names TEXT,
                has_elevator_filing BOOLEAN NOT NULL DEFAULT FALSE,
                elevator_device_types TEXT,
                elevator_work_types TEXT,
                permit_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS project_status_events (
                id BIGSERIAL PRIMARY KEY,
                project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                old_status VARCHAR(100),
                new_status VARCHAR(100),
                event_at TIMESTAMP NOT NULL,
                source VARCHAR(50),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for column, ddl in (
            ("has_electrical_filing", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("electrical_service_work", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("electrical_general_wiring", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("electrical_lighting_work", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("electrical_temp_power", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("electrical_hvac_or_boiler_wiring", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("electrical_new_meters", "INTEGER"),
            ("electrical_detail_count", "INTEGER NOT NULL DEFAULT 0"),
            ("electrical_scope_categories", "TEXT"),
            ("electrical_floor_names", "TEXT"),
            ("has_elevator_filing", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("elevator_device_types", "TEXT"),
            ("elevator_work_types", "TEXT"),
            ("design_professional_business_name", "VARCHAR(255)"),
            ("design_professional_person_name", "VARCHAR(225)"),
            ("design_professional_license", "VARCHAR(100)"),
        ):
            cur.execute(f"ALTER TABLE projects ADD COLUMN IF NOT EXISTS {column} {ddl}")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sales_alerts (
                id BIGSERIAL PRIMARY KEY,
                project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                permit_id BIGINT,
                alert_type VARCHAR(50) NOT NULL,
                title VARCHAR(255) NOT NULL,
                summary TEXT,
                old_value TEXT,
                new_value TEXT,
                event_at TIMESTAMP NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'new',
                assigned_to VARCHAR(255),
                reviewed_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Electrical Details has many child rows per application.  Keeping its
        # published row identity prevents scope items/floors from being
        # flattened into a single permit row or double-counted on reingest.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS electrical_permit_details (
                unique_id TEXT PRIMARY KEY,
                work_id TEXT,
                floor_id TEXT,
                sign_id TEXT,
                job_filing_number VARCHAR(100) NOT NULL,
                normalized_filing_number VARCHAR(100) NOT NULL,
                project_key VARCHAR(140) NOT NULL,
                work_description TEXT,
                item TEXT,
                item_quantity NUMERIC(16,3),
                item_cost NUMERIC(16,2),
                fee_amount NUMERIC(16,2),
                item_detail TEXT,
                floor_name TEXT,
                from_floor TEXT,
                to_floor TEXT,
                floor_detail TEXT,
                floor_fixtures INTEGER,
                floor_ac_receptacles INTEGER,
                floor_att_receptacles INTEGER,
                floor_switches INTEGER,
                floor_outlets INTEGER,
                floor_motors_generators INTEGER,
                floor_hpkw NUMERIC(16,3),
                floor_heaters INTEGER,
                floor_kw NUMERIC(16,3),
                floor_transformers INTEGER,
                floor_kva NUMERIC(16,3),
                sign_dimensions TEXT,
                sign_sq_footage NUMERIC(16,3),
                sign_circuits INTEGER,
                sign_lamps INTEGER,
                sign_lamp_wattage NUMERIC(16,3),
                sign_transformers INTEGER,
                sign_va_per_transformer NUMERIC(16,3),
                sign_total_watts_va NUMERIC(16,3),
                sign_total_aw_guage TEXT,
                sign_sockets_per_circuit INTEGER,
                sign_materials_guage TEXT,
                sign_text TEXT,
                sign_manufacturer TEXT,
                sign_manufacturer_address TEXT,
                ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # City Record notices are pre-permit evidence, not DOB permits.  They
        # remain standalone until a later, evidence-backed graph resolver can
        # connect a notice to an organization, property, or project.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS external_project_signals (
                id BIGSERIAL PRIMARY KEY,
                source VARCHAR(50) NOT NULL,
                source_record_id VARCHAR(100) NOT NULL,
                signal_type VARCHAR(100),
                title TEXT NOT NULL,
                description TEXT,
                agency_name VARCHAR(255),
                category VARCHAR(255),
                selection_method VARCHAR(255),
                section_name VARCHAR(255),
                pin VARCHAR(100),
                notice_date DATE,
                end_date DATE,
                due_date TIMESTAMP,
                event_date TIMESTAMP,
                contact_name VARCHAR(255),
                contact_phone VARCHAR(100),
                contact_email VARCHAR(255),
                vendor_name VARCHAR(255),
                vendor_address TEXT,
                contract_amount NUMERIC(18,2),
                building_name VARCHAR(255),
                street_address_1 TEXT,
                street_address_2 TEXT,
                city VARCHAR(100),
                state VARCHAR(50),
                zip_code VARCHAR(20),
                source_url TEXT,
                relevance_score INTEGER NOT NULL DEFAULT 0,
                relevance_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                matched_project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL,
                review_status VARCHAR(30) NOT NULL DEFAULT 'new',
                ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, source_record_id)
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_project_key ON permits(project_key)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_project_id ON permits(project_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_status_date ON permits(current_status_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_applicant_business ON permits(applicant_business_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_related_job ON permits(related_job_number)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_status_date ON projects(current_status_date DESC)")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_projects_bbl_status_date
            ON projects(bbl, current_status_date DESC NULLS LAST)
            WHERE bbl IS NOT NULL
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_alerts_queue ON sales_alerts(status, event_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_electrical_details_project ON electrical_permit_details(project_key)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_electrical_details_filing ON electrical_permit_details(normalized_filing_number)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_external_signals_queue ON external_project_signals(review_status, relevance_score DESC, notice_date DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_external_signals_due ON external_project_signals(due_date) WHERE due_date IS NOT NULL")

        # User-owned account/project/property watchlists.  These deliberately
        # use a typed external key rather than a foreign key to projects: a rep
        # can watch an account before its first DOB project arrives.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlists (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name VARCHAR(120) NOT NULL,
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                digest_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                last_digest_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_watchlists_default_per_user
            ON watchlists(user_id) WHERE is_default = TRUE
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_items (
                id BIGSERIAL PRIMARY KEY,
                watchlist_id BIGINT NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
                entity_type VARCHAR(30) NOT NULL CHECK (
                    entity_type IN ('buyer', 'project', 'property')
                ),
                entity_key VARCHAR(255) NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(watchlist_id, entity_type, entity_key)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_digests (
                id BIGSERIAL PRIMARY KEY,
                watchlist_id BIGINT NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
                period_start TIMESTAMP NOT NULL,
                period_end TIMESTAMP NOT NULL,
                event_count INTEGER NOT NULL DEFAULT 0,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                delivery_status VARCHAR(30) NOT NULL DEFAULT 'stored',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(watchlist_id, period_start, period_end)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_watchlists_user ON watchlists(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_items_lookup ON watchlist_items(entity_type, entity_key)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_digests_list ON watchlist_digests(watchlist_id, period_end DESC)")
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_project_status_event
            ON project_status_events(project_id, new_status, event_at)
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_alert_event
            ON sales_alerts(project_id, alert_type, event_at)
        """)

        # Repair identities written by the older importer.  Approved-permit
        # applicant values were previously put into permittee_* columns even
        # though the source labels them as applicants.  Preserve their licence
        # data but move the names to the correctly-labelled raw fields.
        cur.execute("""
            UPDATE permits
            SET applicant_first_name = permittee_first_name,
                applicant_last_name = permittee_last_name,
                applicant_business_name = permittee_business_name,
                participant_role = 'permit_applicant',
                permittee_first_name = NULL,
                permittee_last_name = NULL,
                permittee_business_name = NULL
            WHERE api_source = 'dob_now_approved'
              AND participant_role IS NULL
              AND COALESCE(permittee_first_name, permittee_last_name,
                           permittee_business_name) IS NOT NULL
        """)
        cur.execute("""
            UPDATE permits
            SET participant_role = 'legacy_applicant'
            WHERE api_source = 'dob_now_filings'
              AND participant_role IS NULL
        """)
        cur.execute("""
            UPDATE permits
            SET participant_role_confidence = CASE
                WHEN participant_role IN ('permittee', 'filing_applicant',
                                          'permit_applicant', 'owner') THEN 1.000
                WHEN participant_role = 'legacy_applicant' THEN 0.350
                ELSE 0.700
            END
            WHERE participant_role_confidence IS NULL
              AND participant_role IS NOT NULL
        """)

        # Preserve applicant as a compatibility/display field, but never use it
        # as proof that an entity is a contractor.  Each row in this view has an
        # explicit source role and the raw identity fields remain available.
        # Recreate this derived view instead of CREATE OR REPLACE.  Earlier
        # releases exposed varchar typmods inherited from the raw columns;
        # btrim/UNION expressions below correctly resolve to text, which
        # PostgreSQL will not accept as an in-place view-column type change.
        # No data lives in the view, and omitting CASCADE protects any
        # unexpected downstream database dependency.
        cur.execute("DROP VIEW IF EXISTS permit_participants")
        cur.execute("""
            CREATE VIEW permit_participants AS
            SELECT * FROM (
                SELECT p.id AS permit_id, p.project_id, p.project_key, p.api_source,
                       'permittee'::varchar AS role,
                       NULLIF(btrim(p.permittee_business_name), '') AS business_name,
                       NULLIF(btrim(concat_ws(' ', p.permittee_first_name,
                                                  p.permittee_last_name)), '') AS person_name,
                       NULL::varchar AS professional_title,
                       p.permittee_license_type AS license_type,
                       p.permittee_license_number AS license_number,
                       p.permittee_phone AS phone,
                       1.000::numeric AS role_confidence,
                       CASE
                           WHEN UPPER(COALESCE(p.permittee_license_type, '')) IN
                                ('GC', 'GENERAL CONTRACTOR')
                                OR p.permittee_license_type ILIKE '%%GENERAL CONTRACTOR%%'
                               THEN 0.950
                           WHEN p.permittee_license_number IS NOT NULL THEN 0.700
                           ELSE 0.450
                       END::numeric AS contractor_confidence,
                       'DOB source explicitly labels this party as permittee'::text AS confidence_reason
                FROM permits p
                UNION ALL
                SELECT p.id, p.project_id, p.project_key, p.api_source,
                       COALESCE(NULLIF(p.participant_role, ''), 'applicant')::varchar,
                       NULLIF(btrim(p.applicant_business_name), ''),
                       COALESCE(
                           NULLIF(btrim(concat_ws(' ', p.applicant_first_name,
                                                      p.applicant_last_name)), ''),
                           CASE WHEN p.api_source LIKE 'dob_now%%'
                                THEN NULLIF(btrim(p.applicant), '') END
                       ),
                       p.applicant_professional_title,
                       p.permittee_license_type,
                       p.permittee_license_number,
                       NULL::varchar,
                       COALESCE(p.participant_role_confidence,
                           CASE WHEN p.participant_role = 'legacy_applicant' THEN 0.350
                                ELSE 0.900 END)::numeric,
                       CASE
                           WHEN p.participant_role = 'electrical_contractor' THEN 0.990
                           WHEN p.participant_role = 'elevator_applicant'
                                AND p.permittee_license_number IS NOT NULL THEN 0.800
                           WHEN p.participant_role = 'permit_applicant' AND (
                               UPPER(COALESCE(p.permittee_license_type, '')) IN
                                   ('GC', 'GENERAL CONTRACTOR')
                               OR p.permittee_license_type ILIKE '%%GENERAL CONTRACTOR%%'
                           ) THEN 0.900
                           WHEN p.participant_role = 'legacy_applicant' THEN 0.150
                           ELSE 0.250
                       END::numeric,
                       CASE
                           WHEN p.participant_role = 'electrical_contractor'
                               THEN 'Electrical dataset explicitly identifies a licensed electrician and firm'
                           WHEN p.participant_role = 'legacy_applicant'
                               THEN 'Legacy overloaded applicant field; role is uncertain'
                           ELSE 'Role comes from the source field label; contractor status requires licence evidence'
                       END::text
                FROM permits p
                UNION ALL
                SELECT p.id, p.project_id, p.project_key, p.api_source,
                       'filing_representative'::varchar,
                       NULLIF(btrim(p.filing_representative_business_name), ''),
                       NULLIF(btrim(concat_ws(' ', p.filing_representative_first_name,
                                                  p.filing_representative_last_name)), ''),
                       NULL::varchar, NULL::varchar, NULL::varchar, NULL::varchar,
                       1.000::numeric, 0.050::numeric,
                       'DOB source explicitly labels this party as filing representative'::text
                FROM permits p
                UNION ALL
                SELECT p.id, p.project_id, p.project_key, p.api_source,
                       'design_professional'::varchar,
                       NULLIF(btrim(p.design_professional_business_name), ''),
                       NULLIF(btrim(concat_ws(' ', p.design_professional_first_name,
                                                  p.design_professional_last_name)), ''),
                       NULL::varchar, NULL::varchar,
                       p.design_professional_license, NULL::varchar,
                       1.000::numeric, 0.050::numeric,
                       'DOB source explicitly labels this party as design professional'::text
                FROM permits p
                UNION ALL
                SELECT p.id, p.project_id, p.project_key, p.api_source,
                       'owner'::varchar,
                       NULLIF(btrim(p.owner_business_name), ''),
                       NULLIF(btrim(concat_ws(' ', p.owner_first_name,
                                                  p.owner_last_name)), ''),
                       NULL::varchar, NULL::varchar, NULL::varchar, p.owner_phone,
                       1.000::numeric, 0.050::numeric,
                       'DOB source explicitly labels this party as owner'::text
                FROM permits p
            ) participants
            WHERE COALESCE(business_name, person_name) IS NOT NULL
        """)

        # Backfill stable project identities for historical rows.  Raw sources
        # stay separate so a coincidentally identical BIS/DOB NOW number cannot
        # merge two unrelated jobs.
        cur.execute("""
            UPDATE permits
            SET project_key = CASE
                WHEN api_source = 'dob_now_electrical'
                    THEN 'DOBNOW-ELECTRICAL:' || split_part(
                        COALESCE(NULLIF(btrim(job_number), ''), permit_no), '-', 1)
                WHEN api_source = 'dob_now_elevator'
                    THEN 'DOBNOW-ELEVATOR:' || split_part(
                        COALESCE(NULLIF(btrim(job_number), ''), permit_no), '-', 1)
                WHEN api_source LIKE 'dob_now%%'
                    THEN 'DOBNOW:' || split_part(
                        COALESCE(NULLIF(btrim(job_number), ''), permit_no), '-', 1)
                ELSE 'BIS:' || COALESCE(NULLIF(btrim(job_number), ''), permit_no)
            END
            WHERE (project_key IS NULL OR (
                    api_source LIKE 'dob_now%%' AND project_key LIKE 'DOBNOW:%%-%%'
                ) OR (
                    api_source = 'dob_now_electrical'
                    AND project_key NOT LIKE 'DOBNOW-ELECTRICAL:%%'
                  ) OR (
                    api_source = 'dob_now_elevator'
                    AND project_key NOT LIKE 'DOBNOW-ELEVATOR:%%'
                  ))
              AND COALESCE(NULLIF(btrim(job_number), ''), permit_no) IS NOT NULL
        """)
        conn.commit()
    except Exception:
        if not conn.closed:
            conn.rollback()
        raise
    finally:
        cur.close()


PROJECT_AGGREGATE_SQL = """
    WITH scoped AS (
        SELECT p.*,
               COALESCE(p.current_status_date,
                        p.issue_date::timestamp,
                        p.filing_date::timestamp,
                        p.api_last_updated) AS activity_at,
               COALESCE(NULLIF(p.filing_status, ''),
                        NULLIF(p.permit_status, ''),
                        NULLIF(p.status, '')) AS activity_status
        FROM permits p
        WHERE p.project_key = ANY(%s)
    ), latest AS (
        SELECT DISTINCT ON (project_key)
               project_key, api_source, job_number, bbl, bin, address, borough,
               job_type, work_description, activity_status, activity_at,
               owner_business_name, applicant_business_name,
               NULLIF(btrim(concat_ws(' ', applicant_first_name,
                                          applicant_last_name)), '') AS applicant_person_name,
               applicant_professional_title,
               filing_representative_business_name
        FROM scoped
        ORDER BY project_key, activity_at DESC NULLS LAST, id DESC
    ), totals AS (
        SELECT project_key,
               MAX(initial_cost) AS initial_cost,
               MAX(existing_stories_count) AS existing_stories_count,
               MAX(proposed_stories_count) AS proposed_stories_count,
               MAX(existing_dwelling_units) AS existing_dwelling_units,
               MAX(proposed_dwelling_units) AS proposed_dwelling_units,
               MIN(filing_date) AS first_filing_date,
               MAX(issue_date) AS latest_issue_date,
               BOOL_OR(COALESCE(api_source = 'dob_now_electrical', FALSE))
                   AS has_electrical_filing,
               BOOL_OR(COALESCE(electrical_service_work, FALSE)) AS electrical_service_work,
               BOOL_OR(COALESCE(electrical_general_wiring, FALSE)) AS electrical_general_wiring,
               BOOL_OR(COALESCE(electrical_lighting_work, FALSE)) AS electrical_lighting_work,
               BOOL_OR(COALESCE(electrical_temp_construction_service, FALSE)
                       OR COALESCE(electrical_temp_light_power, FALSE)) AS electrical_temp_power,
               BOOL_OR(COALESCE(electrical_hvac_wiring, FALSE)
                       OR COALESCE(electrical_boiler_burner_wiring, FALSE))
                   AS electrical_hvac_or_boiler_wiring,
               MAX(electrical_new_meters) AS electrical_new_meters,
               BOOL_OR(COALESCE(api_source = 'dob_now_elevator', FALSE))
                   AS has_elevator_filing,
               STRING_AGG(DISTINCT NULLIF(btrim(elevator_device_type), ''), '; '
                          ORDER BY NULLIF(btrim(elevator_device_type), ''))
                   AS elevator_device_types,
               STRING_AGG(DISTINCT NULLIF(btrim(elevator_work_type), ''), '; '
                          ORDER BY NULLIF(btrim(elevator_work_type), ''))
                   AS elevator_work_types,
               (ARRAY_AGG(NULLIF(btrim(owner_business_name), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(owner_business_name), '') IS NOT NULL))[1]
                   AS owner_business_name,
               (ARRAY_AGG(NULLIF(btrim(applicant_business_name), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(applicant_business_name), '') IS NOT NULL))[1]
                   AS applicant_business_name,
               (ARRAY_AGG(NULLIF(btrim(concat_ws(' ', applicant_first_name,
                                                    applicant_last_name)), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(concat_ws(' ', applicant_first_name,
                                                            applicant_last_name)), '') IS NOT NULL))[1]
                   AS applicant_person_name,
               (ARRAY_AGG(NULLIF(btrim(applicant_professional_title), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(applicant_professional_title), '') IS NOT NULL))[1]
                   AS applicant_professional_title,
               (ARRAY_AGG(NULLIF(btrim(filing_representative_business_name), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(filing_representative_business_name), '') IS NOT NULL))[1]
                   AS filing_representative_business_name,
               (ARRAY_AGG(NULLIF(btrim(design_professional_business_name), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(design_professional_business_name), '') IS NOT NULL))[1]
                   AS design_professional_business_name,
               (ARRAY_AGG(NULLIF(btrim(concat_ws(' ', design_professional_first_name,
                                                    design_professional_last_name)), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(concat_ws(' ', design_professional_first_name,
                                                            design_professional_last_name)), '') IS NOT NULL))[1]
                   AS design_professional_person_name,
               (ARRAY_AGG(NULLIF(btrim(design_professional_license), '')
                    ORDER BY activity_at DESC NULLS LAST, id DESC)
                    FILTER (WHERE NULLIF(btrim(design_professional_license), '') IS NOT NULL))[1]
                   AS design_professional_license,
               COUNT(*)::integer AS permit_count
        FROM scoped
        GROUP BY project_key
    ), electrical_details AS (
        SELECT ed.project_key,
               COUNT(*)::integer AS detail_count,
               STRING_AGG(DISTINCT NULLIF(btrim(ed.work_description), ''), '; '
                          ORDER BY NULLIF(btrim(ed.work_description), ''))
                   AS scope_categories,
               STRING_AGG(DISTINCT NULLIF(btrim(COALESCE(
                              ed.floor_name, ed.floor_detail, ed.from_floor)), ''), '; '
                          ORDER BY NULLIF(btrim(COALESCE(
                              ed.floor_name, ed.floor_detail, ed.from_floor)), ''))
                   AS floor_names
        FROM electrical_permit_details ed
        JOIN (SELECT DISTINCT project_key FROM scoped) s USING (project_key)
        GROUP BY ed.project_key
    )
    SELECT l.project_key,
           CASE WHEN l.project_key LIKE 'DOBNOW-ELECTRICAL:%%' THEN 'DOB NOW Electrical'
                WHEN l.project_key LIKE 'DOBNOW-ELEVATOR:%%' THEN 'DOB NOW Elevator'
                WHEN l.project_key LIKE 'DOBNOW:%%' THEN 'DOB NOW'
                ELSE 'BIS' END,
           l.job_number, l.bbl, l.bin, l.address, l.borough, l.job_type,
           l.work_description, t.initial_cost, t.existing_stories_count,
           t.proposed_stories_count, t.existing_dwelling_units,
           t.proposed_dwelling_units, t.first_filing_date, t.latest_issue_date,
           l.activity_status, l.activity_at, t.owner_business_name,
           t.applicant_business_name, t.applicant_person_name,
           t.applicant_professional_title,
           t.filing_representative_business_name,
           t.design_professional_business_name,
           t.design_professional_person_name,
           t.design_professional_license,
           t.has_electrical_filing, t.electrical_service_work,
           t.electrical_general_wiring, t.electrical_lighting_work,
           t.electrical_temp_power, t.electrical_hvac_or_boiler_wiring,
           t.electrical_new_meters,
           COALESCE(ed.detail_count, 0), ed.scope_categories, ed.floor_names,
           t.has_elevator_filing, t.elevator_device_types,
           t.elevator_work_types, t.permit_count
    FROM latest l
    JOIN totals t USING (project_key)
    LEFT JOIN electrical_details ed USING (project_key)
"""


PROJECT_COLUMNS = (
    "project_key", "source_system", "job_number", "bbl", "bin", "address",
    "borough", "job_type", "work_description", "initial_cost",
    "existing_stories_count", "proposed_stories_count",
    "existing_dwelling_units", "proposed_dwelling_units",
    "first_filing_date", "latest_issue_date", "current_status",
    "current_status_date", "owner_business_name", "applicant_business_name",
    "applicant_person_name", "applicant_professional_title",
    "filing_representative_business_name", "design_professional_business_name",
    "design_professional_person_name", "design_professional_license",
    "has_electrical_filing",
    "electrical_service_work", "electrical_general_wiring",
    "electrical_lighting_work", "electrical_temp_power",
    "electrical_hvac_or_boiler_wiring", "electrical_new_meters",
    "electrical_detail_count", "electrical_scope_categories",
    "electrical_floor_names", "has_elevator_filing",
    "elevator_device_types", "elevator_work_types",
    "permit_count",
)


def _alert(cur, project_id, alert_type, title, summary, old_value, new_value,
           event_at):
    cur.execute("""
        INSERT INTO sales_alerts
            (project_id, alert_type, title, summary, old_value, new_value, event_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (project_id, alert_type, event_at) DO NOTHING
    """, (project_id, alert_type, title, summary, old_value, new_value, event_at))


def sync_project_intelligence(conn, project_keys: Iterable[str],
                              alert_window_days: int = 30) -> dict:
    """Refresh touched projects and create only actionable, deduplicated alerts."""
    keys = sorted({key for key in project_keys if key})
    if not keys:
        return {"projects": 0, "alerts": 0}

    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, project_key, current_status, current_status_date,
                   latest_issue_date
            FROM projects WHERE project_key = ANY(%s)
        """, (keys,))
        old = {
            row[1]: {
                "id": row[0], "status": row[2], "status_date": row[3],
                "latest_issue_date": row[4],
            }
            for row in cur.fetchall()
        }

        cur.execute(PROJECT_AGGREGATE_SQL, (keys,))
        rows = cur.fetchall()
        if not rows:
            conn.commit()
            return {"projects": 0, "alerts": 0}
        columns_sql = ", ".join(PROJECT_COLUMNS)
        updates_sql = ", ".join(
            f"{column} = EXCLUDED.{column}"
            for column in PROJECT_COLUMNS if column != "project_key"
        )
        execute_values(cur, f"""
            INSERT INTO projects ({columns_sql}) VALUES %s
            ON CONFLICT (project_key) DO UPDATE SET
                {updates_sql}, updated_at = CURRENT_TIMESTAMP
        """, rows, page_size=1000)

        cur.execute("""
            UPDATE permits p
            SET project_id = pr.id
            FROM projects pr
            WHERE p.project_key = pr.project_key
              AND p.project_key = ANY(%s)
              AND p.project_id IS DISTINCT FROM pr.id
        """, (keys,))

        cur.execute("""
            SELECT id, project_key, address, current_status, current_status_date,
                   first_filing_date, latest_issue_date, initial_cost
            FROM projects WHERE project_key = ANY(%s)
        """, (keys,))
        current = cur.fetchall()
        cutoff = datetime.now() - timedelta(days=alert_window_days)
        alerts = []
        status_events = []
        for (project_id, key, address, status, status_date, first_filing,
             latest_issue, initial_cost) in current:
            previous = old.get(key)
            event_at = status_date or (
                datetime.combine(latest_issue, datetime.min.time())
                if latest_issue else datetime.now()
            )
            place = address or key
            cost_text = f" Estimated cost: ${initial_cost:,.0f}." if initial_cost else ""

            if previous is None:
                first_activity = status_date
                if first_activity is None and first_filing:
                    first_activity = datetime.combine(first_filing, datetime.min.time())
                if first_activity and first_activity >= cutoff:
                    alerts.append(
                        (project_id, "new_filing", f"New DOB project: {place}",
                         f"A new project entered DOB at {place}. Status: {status or 'unknown'}.{cost_text}",
                         None, status, event_at)
                    )
            else:
                status_changed = (
                    status and status != previous["status"] and
                    (status_date is None or previous["status_date"] is None or
                     status_date >= previous["status_date"])
                )
                if status_changed:
                    status_events.append(
                        (project_id, previous["status"], status, event_at,
                         "nyc_open_data")
                    )
                    if event_at >= cutoff:
                        alerts.append(
                            (project_id, "status_change",
                             f"DOB status changed: {place}",
                             f"{place} moved from {previous['status'] or 'unknown'} to {status}.{cost_text}",
                             previous["status"], status, event_at)
                        )

                if latest_issue and not previous["latest_issue_date"]:
                    issue_at = datetime.combine(latest_issue, datetime.min.time())
                    if issue_at >= cutoff:
                        alerts.append(
                            (project_id, "permit_issued",
                             f"Permit issued: {place}",
                             f"A permit was issued for {place} on {latest_issue:%b %d, %Y}.{cost_text}",
                             None, str(latest_issue), issue_at)
                        )

        if status_events:
            execute_values(cur, """
                INSERT INTO project_status_events
                    (project_id, old_status, new_status, event_at, source)
                VALUES %s
                ON CONFLICT (project_id, new_status, event_at) DO NOTHING
            """, status_events, page_size=5000)
        if alerts:
            execute_values(cur, """
                INSERT INTO sales_alerts
                    (project_id, alert_type, title, summary, old_value,
                     new_value, event_at)
                VALUES %s
                ON CONFLICT (project_id, alert_type, event_at) DO NOTHING
            """, alerts, page_size=5000)

        conn.commit()
        return {"projects": len(rows), "alerts": len(alerts)}
    except Exception:
        if not conn.closed:
            conn.rollback()
        raise
    finally:
        cur.close()
