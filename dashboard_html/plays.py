"""
Prebuilt filter "plays" for the properties page.

A play is a named, goal-first filter over the signal columns: the name
says what you're hunting, the description says why it works, and the
guide says exactly what to do with the results. The WHERE fragments are
server-defined constants (never user input) and are appended to the same
filter pipeline the list, export, and bulk-enrich endpoints share — so
activating a play, exporting it, and bulk-enriching it all operate on
exactly the same set of buildings.

Each play declares required_columns; a play is only offered when the
migration that adds those columns has run, so a pre-migration database
simply shows fewer plays instead of erroring.
"""

# Every play:
#   id                stable identifier used in the ?play= query param
#   name              the goal, phrased as the thing you're doing
#   description       one or two sentences on why this list makes money
#   how_to_use        short numbered steps
#   where             SQL fragment over buildings b (server constant)
#   required_columns  buildings columns that must exist to offer the play
#   recommended_sort  optional {'by': ..., 'order': ...} using API sort keys
#   audience          'investors' | 'contractors' | 'both' (display chip)
#   family            optional UI grouping; existing plays remain "property_intel"
#   required_permit_columns  optional permit columns needed by the WHERE clause
#   data_source       source family used for coverage messaging in the UI
#   coverage_where    optional SQL fragment that proves the source data exists
#   coverage_required_columns / coverage_required_permit_columns
#                     columns needed to calculate coverage (not play availability)
#   coverage_label    short, user-facing explanation of what coverage means
#   coverage_kind     'pipeline' when every building is expected to be refreshed;
#                     'source' when the count describes a naturally smaller feed
#   permit_count_where / permit_coverage_where
#                     optional predicates over permits p used only to aggregate
#                     all permit-backed card counts in one database pass

PLAYS = [
    {
        'id': 'speculation-watch',
        'name': 'Work the Speculation Watch List',
        'description': ('Rent-regulated buildings the city flagged because the purchase price '
                        'doesn\'t pencil on current rents — meaning the buyer has a plan. '
                        'A city-curated list of investors in motion and buildings about to see activity.'),
        'audience': 'both',
        'data_source': 'signals',
        'where': "b.on_speculation_watch_list = TRUE",
        'required_columns': ['on_speculation_watch_list'],
        'coverage_where': "b.signals_enrichment_version >= 2",
        'coverage_required_columns': ['signals_enrichment_version'],
        'coverage_label': 'Signal enrichment complete',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'sale_date', 'order': 'desc'},
        'how_to_use': [
            'Sort by sale date — the newest purchases are the hottest window.',
            'Open a building and identify the buyer, then use the SOS principal to get the person behind the LLC.',
            'Enrich that buyer\'s contact — they are actively deploying capital right now.',
            'Watch the building\'s permits and complaints to time your pitch.',
        ],
        # The full playbook rendered when this play is active.
        'playbook': {
            'what': ('Under Local Law 7 of 2018, HPD evaluates every sale of certain rent-regulated '
                     'buildings. When the price implies current rents can\'t service the deal, the '
                     'building goes on the Speculation Watch List: the buyer is betting on turnover, '
                     'renovation, or repositioning. That makes this two lead lists in one.'),
            'ways': [
                {'title': 'The buyers are prospects',
                 'body': ('Everyone on this list just closed an aggressive acquisition. They buy again, '
                          'they need financing, management, legal, and off-market deal flow. Pitch them '
                          'as active investors — not distressed owners.')},
                {'title': 'The buildings are pipeline',
                 'body': ('Repositioning means work. Expect alteration permits 6–18 months after the '
                          'sale — contractors who show up before the filings win the job. Rising HPD '
                          'complaints or violations signal tenant friction and a management or legal '
                          'services opening.')},
            ],
            'steps': [
                'Activate this play with no other filters to see every flagged building you track.',
                'Sort by sale date, newest first. A purchase in the last 6 months is a deal still being planned — the best moment to reach the buyer.',
                'Open the building profile. The buyer on the latest deed is your lead; if it\'s an LLC, the SOS principal lookup gives you the human.',
                'Click Enrich on the buyer to pull phone and email. This is the highest-value enrichment spend in the product — you know this person is transacting.',
                'Read the building\'s signals before the call: new permits mean renovation already started (pitch contracting trades now); complaint/violation upticks mean tenant pressure (pitch management or legal); a flagged building with no activity after a year may be a stalled plan (pitch an acquisition).',
                'Export the play with owner and enriched-contact fields and run it as its own campaign — this list updates as HPD adds sales, so re-check monthly.',
            ],
            'caution': ('This is not a distress list — these owners are typically well-capitalized. '
                        'And the list exists because of tenant-displacement concerns, so pitch '
                        'services and deals; never imply the owner is doing something wrong.'),
        },
    },
    {
        'id': 'development-upside',
        'name': 'Find underbuilt lots',
        'description': ('Zoning allows at least one full FAR more than what\'s built — the owner is '
                        'sitting on buildable square footage that developers pay land-value premiums for. '
                        'Unused FAR × lot size = the hidden asset.'),
        'audience': 'investors',
        'data_source': 'pluto',
        'where': "b.unused_far >= 1.0 AND b.lot_sqft > 0",
        'required_columns': ['unused_far', 'lot_sqft'],
        'coverage_where': "b.property_last_enriched IS NOT NULL",
        'coverage_required_columns': ['property_last_enriched'],
        'coverage_label': 'Property facts refreshed',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'unused_far', 'order': 'desc'},
        'how_to_use': [
            'Sort by unused FAR; multiply by lot sqft to rank absolute buildable area.',
            'Cross with senior-exemption or long-held owners — they rarely know what the dirt is worth.',
            'Pitch on land value, not building value: "your lot supports N more square feet."',
            'Small buildings on big allowances (a 1-story taxpayer in an R7) are the classic assemblage target.',
        ],
    },
    {
        'id': 'free-and-clear',
        'name': 'Owners who can sell tomorrow',
        'description': ('Every recorded mortgage has been satisfied — no payoff, no lender approval, '
                        'maximum equity. These owners can transact fast, and they hear from nobody '
                        'because they never refinance.'),
        'audience': 'investors',
        'data_source': 'acris',
        'where': "b.is_free_and_clear = TRUE AND b.acris_total_transactions > 0",
        'required_columns': ['is_free_and_clear', 'acris_total_transactions'],
        'coverage_where': "b.acris_last_enriched IS NOT NULL",
        'coverage_required_columns': ['acris_last_enriched'],
        'coverage_label': 'ACRIS history refreshed',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'value', 'order': 'desc'},
        'how_to_use': [
            'These owners have no bank in the deal — cash offers and quick closes land well.',
            'Check the last satisfaction date: a recent payoff is a life-event signal.',
            'Combine with the senior-owners play for the strongest motivated-seller overlap.',
        ],
    },
    {
        'id': 'senior-owners',
        'name': 'Long-tenured senior owners',
        'description': ('Properties carrying a senior citizen homeowner exemption (SCHE) — '
                        'owner-occupants 65+, usually decades of tenure and equity. The highest-'
                        'converting seller demographic in the business.'),
        'audience': 'investors',
        'data_source': 'signals',
        'where': "b.has_senior_exemption = TRUE",
        'required_columns': ['has_senior_exemption'],
        'coverage_where': "b.signals_enrichment_version >= 2",
        'coverage_required_columns': ['signals_enrichment_version'],
        'coverage_label': 'Signal enrichment complete',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'value', 'order': 'desc'},
        'how_to_use': [
            'Lead with patience and simplicity: as-is offers, flexible timelines, no showings.',
            'Cross with free-and-clear for owners who can close without a bank.',
            'The HPD registered mailing address is on the profile — direct mail still wins this demographic.',
        ],
    },
    {
        'id': 'city-pressure',
        'name': 'Owners under city pressure',
        'description': ('An open housing-court case, a current lien-sale notice, or marshal evictions '
                        'on record. Each one is the city or the courts making ownership expensive — '
                        'classic motivated-seller signals.'),
        'audience': 'investors',
        'data_source': 'signals',
        'where': ("(b.litigation_open_count > 0 OR b.eviction_count > 0 "
                  "OR b.has_tax_delinquency = TRUE)"),
        'required_columns': [
            'litigation_open_count', 'eviction_count', 'has_tax_delinquency'],
        'coverage_where': ("b.signals_enrichment_version >= 2 "
                           "AND b.tax_lien_last_checked IS NOT NULL"),
        'coverage_required_columns': [
            'signals_enrichment_version', 'tax_lien_last_checked'],
        'coverage_label': 'Court, eviction, and lien sources refreshed',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'sale_date', 'order': 'asc'},
        'how_to_use': [
            'Check which signal fired on the profile — litigation, lien notice, and evictions are different conversations.',
            'A lien-sale notice has a hard deadline: the city sells the lien if it isn\'t cured. Time-boxed motivation.',
            'Long-held properties under pressure are the strongest exits; recent buyers under pressure may just be repositioning.',
        ],
    },
    {
        'id': 'just-completed',
        'name': 'Projects that just finished',
        'description': ('A Certificate of Occupancy issued in the last 6 months — construction is done. '
                        'Owners here are at the exit, refinance, or lease-up moment, and the data on '
                        'these buildings is the freshest in the system.'),
        'audience': 'both',
        'data_source': 'signals',
        'where': "b.latest_co_date >= CURRENT_DATE - INTERVAL '180 days'",
        'required_columns': ['latest_co_date'],
        'coverage_where': "b.signals_enrichment_version >= 2",
        'coverage_required_columns': ['signals_enrichment_version'],
        'coverage_label': 'Signal enrichment complete',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'co_date', 'order': 'desc'},
        'how_to_use': [
            'A TCO (temporary CO) means punch-list work remains — still a contractor opportunity.',
            'A final CO on a rental building means lease-up: management, brokerage, and refi pitches.',
            'The GC who finished the job is on the permits — completed projects are their best reference sale.',
        ],
    },
    {
        'id': 'facade-work-due',
        'name': 'Facade work is due',
        'description': ('FISP (Local Law 11) status UNSAFE or SWARMP — the city requires facade repairs '
                        'on a legal clock, with fines for missing it. This is pre-qualified demand for '
                        'exterior work.'),
        'audience': 'contractors',
        'data_source': 'signals',
        'where': "(b.fisp_status ILIKE 'UNSAFE%%' OR b.fisp_status ILIKE 'SWARMP%%')",
        'required_columns': ['fisp_status'],
        'coverage_where': "b.signals_enrichment_version >= 2",
        'coverage_required_columns': ['signals_enrichment_version'],
        'coverage_label': 'Signal enrichment complete',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'value', 'order': 'desc'},
        'how_to_use': [
            'UNSAFE means sidewalk sheds and mandated repairs NOW; SWARMP means repairs required before the next 5-year cycle.',
            'The building owner must hire a QEWI and a facade contractor — reach them before they shortlist.',
            'Bundle with LL97: owners doing facade work often fold in energy upgrades while scaffolding is up.',
        ],
    },
    {
        'id': 'll97-retrofit',
        'name': 'LL97 retrofit exposure',
        'description': ('Buildings estimated to be covered by Local Law 97 (≥25,000 sqft) — facing '
                        'emissions caps with real annual fines. Every one is a candidate for energy '
                        'retrofits, electrification, and compliance work.'),
        'audience': 'contractors',
        'data_source': 'signals',
        'where': "b.ll97_covered_estimated = TRUE",
        'required_columns': ['ll97_covered_estimated'],
        'coverage_where': "b.signals_enrichment_version >= 2",
        'coverage_required_columns': ['signals_enrichment_version'],
        'coverage_label': 'Signal enrichment complete',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'value', 'order': 'desc'},
        'how_to_use': [
            'A low Energy Star score or high site EUI on the profile means bigger fines and an easier sell.',
            'Fines recur annually — frame retrofit cost against cumulative penalties.',
            'Coverage here is estimated by size; confirm against DOB\'s covered-buildings list before quoting.',
        ],
    },
    # Smart Installers plays are additive.  They use the same property filter,
    # export, and enrichment pipeline as the existing investor/contractor plays
    # but are grouped separately in the UI and target building-technology work.
    {
        'id': 'si-major-project-pipeline',
        'name': 'Major project pipeline',
        'description': ('Recent DOB projects with at least $250,000 of stated work. '
                        'These justify a whole-building technology conversation instead of a device quote.'),
        'audience': 'contractors',
        'family': 'smart_installers',
        'data_source': 'permits',
        'where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl "
                  "AND p.initial_cost >= 250000 "
                  "AND COALESCE(p.current_status_date::date, p.filing_date, p.issue_date) "
                  ">= CURRENT_DATE - INTERVAL '540 days')"),
        'permit_count_where': (
            "p.initial_cost >= 250000 AND "
            "COALESCE(p.current_status_date::date, p.filing_date, p.issue_date) "
            ">= CURRENT_DATE - INTERVAL '540 days'"),
        'required_columns': ['bbl'],
        'required_permit_columns': [
            'bbl', 'initial_cost', 'current_status_date', 'filing_date',
            'issue_date'],
        'coverage_where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl "
                           "AND p.initial_cost IS NOT NULL "
                           "AND COALESCE(p.current_status_date::date, p.filing_date, p.issue_date) IS NOT NULL)"),
        'permit_coverage_where': (
            "p.initial_cost IS NOT NULL AND "
            "COALESCE(p.current_status_date::date, p.filing_date, p.issue_date) IS NOT NULL"),
        'coverage_required_permit_columns': [
            'bbl', 'initial_cost', 'current_status_date', 'filing_date',
            'issue_date'],
        'coverage_label': 'Buildings with permit cost and date data',
        'coverage_kind': 'source',
        'recommended_sort': {'by': 'recent_permits', 'order': 'desc'},
        'how_to_use': [
            'Open the latest consolidated DOB project and confirm the proposed use, units, stories, and stage.',
            'Map the owner/developer, GC, architect, engineer, and electrical contractor before outreach.',
            'Lead with a coordinated low-voltage package: network, Wi-Fi, cameras, access, intercom, and infrastructure.',
        ],
    },
    {
        'id': 'si-building-expansion',
        'name': 'Buildings adding units or floors',
        'description': ('Projects where proposed stories or dwelling units exceed existing conditions. '
                        'Growth creates new pathways, doors, residents, network loads, and security scope.'),
        'audience': 'contractors',
        'family': 'smart_installers',
        'data_source': 'permits',
        'where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl AND ("
                  "p.proposed_stories_count > p.existing_stories_count OR "
                  "p.proposed_dwelling_units > p.existing_dwelling_units))"),
        'permit_count_where': (
            "p.proposed_stories_count > p.existing_stories_count OR "
            "p.proposed_dwelling_units > p.existing_dwelling_units"),
        'required_columns': ['bbl'],
        'required_permit_columns': [
            'bbl',
            'existing_stories_count', 'proposed_stories_count',
            'existing_dwelling_units', 'proposed_dwelling_units',
        ],
        'coverage_where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl AND ("
                           "p.existing_stories_count IS NOT NULL OR p.proposed_stories_count IS NOT NULL OR "
                           "p.existing_dwelling_units IS NOT NULL OR p.proposed_dwelling_units IS NOT NULL))"),
        'permit_coverage_where': (
            "p.existing_stories_count IS NOT NULL OR "
            "p.proposed_stories_count IS NOT NULL OR "
            "p.existing_dwelling_units IS NOT NULL OR "
            "p.proposed_dwelling_units IS NOT NULL"),
        'coverage_required_permit_columns': [
            'bbl',
            'existing_stories_count', 'proposed_stories_count',
            'existing_dwelling_units', 'proposed_dwelling_units'],
        'coverage_label': 'Buildings with proposed-condition data',
        'coverage_kind': 'source',
        'recommended_sort': {'by': 'recent_permits', 'order': 'desc'},
        'how_to_use': [
            'Prioritize the largest unit and story deltas and projects still in filing or approval.',
            'Ask for the technology, reflected-ceiling, door, and electrical drawings before pricing.',
            'Offer a single coordinated riser, MDF/IDF, access, intercom, surveillance, and Wi-Fi scope.',
        ],
    },
    {
        'id': 'si-electrical-trigger',
        'name': 'Electrical capacity and wiring triggers',
        'description': ('DOB NOW Electrical filings showing service work, general wiring, temporary power, '
                        'HVAC/boiler wiring, or new meters—strong evidence that a building project is moving.'),
        'audience': 'contractors',
        'family': 'smart_installers',
        'data_source': 'permits',
        'where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl AND "
                  "p.api_source = 'dob_now_electrical' AND ("
                  "p.electrical_service_work OR p.electrical_general_wiring OR "
                  "p.electrical_temp_construction_service OR p.electrical_temp_light_power OR "
                  "p.electrical_hvac_wiring OR p.electrical_boiler_burner_wiring OR "
                  "COALESCE(p.electrical_new_meters, 0) > 0))"),
        'permit_count_where': (
            "p.api_source = 'dob_now_electrical' AND ("
            "p.electrical_service_work OR p.electrical_general_wiring OR "
            "p.electrical_temp_construction_service OR p.electrical_temp_light_power OR "
            "p.electrical_hvac_wiring OR p.electrical_boiler_burner_wiring OR "
            "COALESCE(p.electrical_new_meters, 0) > 0)"),
        'required_columns': ['bbl'],
        'required_permit_columns': [
            'bbl', 'api_source',
            'electrical_service_work', 'electrical_general_wiring',
            'electrical_temp_construction_service', 'electrical_temp_light_power',
            'electrical_hvac_wiring', 'electrical_boiler_burner_wiring',
            'electrical_new_meters',
        ],
        'coverage_where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl "
                           "AND p.api_source = 'dob_now_electrical')"),
        'permit_coverage_where': "p.api_source = 'dob_now_electrical'",
        'coverage_required_permit_columns': ['bbl', 'api_source'],
        'coverage_label': 'Buildings with DOB NOW Electrical filings',
        'coverage_kind': 'source',
        'recommended_sort': {'by': 'recent_permits', 'order': 'desc'},
        'how_to_use': [
            'Use temporary power as an early construction-stage trigger and new meters as a scale signal.',
            'Call the owner/GC about the building package; treat the named electrician as a trade partner, not the buyer by default.',
            'Check whether low-voltage pathways, power, door hardware interfaces, and network rooms are coordinated.',
        ],
    },
    {
        'id': 'si-elevator-modernization',
        'name': 'Elevator modernization and access triggers',
        'description': ('DOB NOW Elevator applications for new installations, alterations, '
                        'replacements, or removals. These projects create access, intercom, '
                        'camera, network, life-safety, and electrical coordination opportunities.'),
        'audience': 'contractors',
        'family': 'smart_installers',
        'data_source': 'permits',
        'where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl "
                  "AND p.api_source = 'dob_now_elevator' "
                  "AND p.filing_date >= CURRENT_DATE - INTERVAL '730 days' "
                  "AND COALESCE(p.elevator_work_type, '') ~* "
                  "'(new installation|alteration|replacement|remove|dismantle)')"),
        'permit_count_where': (
            "p.api_source = 'dob_now_elevator' "
            "AND p.filing_date >= CURRENT_DATE - INTERVAL '730 days' "
            "AND COALESCE(p.elevator_work_type, '') ~* "
            "'(new installation|alteration|replacement|remove|dismantle)'"),
        'required_columns': ['bbl'],
        'required_permit_columns': [
            'bbl', 'api_source', 'filing_date', 'elevator_work_type'],
        'coverage_where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl "
                           "AND p.api_source = 'dob_now_elevator')"),
        'permit_coverage_where': "p.api_source = 'dob_now_elevator'",
        'coverage_required_permit_columns': ['bbl', 'api_source'],
        'coverage_label': 'Buildings with DOB NOW Elevator filings',
        'coverage_kind': 'source',
        'recommended_sort': {'by': 'recent_permits', 'order': 'desc'},
        'how_to_use': [
            'Prioritize active new installations and alteration/replacement filings over signed-off work.',
            'Map the owner, elevator applicant, design professional, and electrical contractor.',
            'Qualify destination dispatch, credential interfaces, cab cameras/intercom, network pathways, and lobby access scope.',
        ],
    },
    {
        'id': 'si-tech-scope-keywords',
        'name': 'Technology scope already visible',
        'description': ('Recent descriptions mentioning intercom, cameras, access control, telecom, '
                        'data, Wi-Fi, security, or door systems. These are explicit or adjacent-fit opportunities.'),
        'audience': 'contractors',
        'family': 'smart_installers',
        'data_source': 'permits',
        'where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl "
                  "AND COALESCE(p.filing_date, p.issue_date) >= CURRENT_DATE - INTERVAL '730 days' "
                  "AND COALESCE(p.work_description, '') ~* "
                  "'(intercom|camera|cctv|access control|telecom|low[ -]?voltage|data cabl|wi[ -]?fi|security|door hardware)')"),
        'permit_count_where': (
            "COALESCE(p.filing_date, p.issue_date) >= CURRENT_DATE - INTERVAL '730 days' "
            "AND COALESCE(p.work_description, '') ~* "
            "'(intercom|camera|cctv|access control|telecom|low[ -]?voltage|data cabl|wi[ -]?fi|security|door hardware)'"),
        'required_columns': ['bbl'],
        'required_permit_columns': [
            'bbl', 'filing_date', 'issue_date', 'work_description'],
        'coverage_where': ("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl "
                           "AND NULLIF(btrim(p.work_description), '') IS NOT NULL)"),
        'permit_coverage_where': (
            "NULLIF(btrim(p.work_description), '') IS NOT NULL"),
        'coverage_required_permit_columns': ['bbl', 'work_description'],
        'coverage_label': 'Buildings with searchable work descriptions',
        'coverage_kind': 'source',
        'recommended_sort': {'by': 'recent_permits', 'order': 'desc'},
        'how_to_use': [
            'Read the description in context; a keyword is a reason to research, not proof of an open bid.',
            'Find who owns the complete technology scope and whether it has already been awarded.',
            'If awarded, pursue adjacent systems, coordination, service, monitoring, and future phases.',
        ],
    },
    {
        'id': 'si-turnover-technology',
        'name': 'Turnover and operations handoff',
        'description': ('Certificates of Occupancy in the last year identify buildings moving from construction '
                        'to lease-up and operations—when access, intercom, cameras, Wi-Fi, and support must work reliably.'),
        'audience': 'contractors',
        'family': 'smart_installers',
        'data_source': 'signals',
        'where': "b.latest_co_date >= CURRENT_DATE - INTERVAL '365 days'",
        'required_columns': ['latest_co_date'],
        'coverage_where': "b.signals_enrichment_version >= 2",
        'coverage_required_columns': ['signals_enrichment_version'],
        'coverage_label': 'Signal enrichment complete',
        'coverage_kind': 'pipeline',
        'recommended_sort': {'by': 'co_date', 'order': 'desc'},
        'how_to_use': [
            'Identify the owner, operator, and property manager taking possession—not only the construction team.',
            'Offer commissioning, closeout cleanup, credentials, remote support, monitoring, and service agreements.',
            'Use completed sites to expand into the buyer’s other buildings.',
        ],
    },
]

_PLAYS_BY_ID = {p['id']: p for p in PLAYS}


def public_play(play):
    """The play as sent to the frontend (everything except the SQL)."""
    return {k: v for k, v in play.items()
            if k not in (
                'where', 'required_columns', 'required_permit_columns',
                'coverage_where', 'coverage_required_columns',
                'coverage_required_permit_columns', 'permit_count_where',
                'permit_coverage_where')}


def available_plays(existing_columns, permit_columns=None):
    """Plays whose required columns exist in this database."""
    permit_columns = permit_columns or set()
    return [p for p in PLAYS
            if all(col in existing_columns for col in p['required_columns'])
            and all(col in permit_columns for col in p.get('required_permit_columns', []))]


def get_play(play_id, existing_columns, permit_columns=None):
    """The play if it exists AND its columns are available, else None."""
    play = _PLAYS_BY_ID.get(play_id)
    if not play:
        return None
    if not all(col in existing_columns for col in play['required_columns']):
        return None
    permit_columns = permit_columns or set()
    if not all(col in permit_columns for col in play.get('required_permit_columns', [])):
        return None
    return play
