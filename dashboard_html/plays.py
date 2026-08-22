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

PLAYS = [
    {
        'id': 'speculation-watch',
        'name': 'Work the Speculation Watch List',
        'description': ('Rent-regulated buildings the city flagged because the purchase price '
                        'doesn\'t pencil on current rents — meaning the buyer has a plan. '
                        'A city-curated list of investors in motion and buildings about to see activity.'),
        'audience': 'both',
        'where': "b.on_speculation_watch_list = TRUE",
        'required_columns': ['on_speculation_watch_list'],
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
        'where': "b.unused_far >= 1.0 AND b.lot_sqft > 0",
        'required_columns': ['unused_far'],
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
        'where': "b.is_free_and_clear = TRUE AND b.acris_total_transactions > 0",
        'required_columns': ['is_free_and_clear'],
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
        'where': "b.has_senior_exemption = TRUE",
        'required_columns': ['has_senior_exemption'],
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
        'where': ("(b.litigation_open_count > 0 OR b.eviction_count > 0 "
                  "OR b.has_tax_delinquency = TRUE)"),
        'required_columns': ['litigation_open_count', 'eviction_count'],
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
        'where': "b.latest_co_date >= CURRENT_DATE - INTERVAL '180 days'",
        'required_columns': ['latest_co_date'],
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
        'where': "(b.fisp_status ILIKE 'UNSAFE%%' OR b.fisp_status ILIKE 'SWARMP%%')",
        'required_columns': ['fisp_status'],
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
        'where': "b.ll97_covered_estimated = TRUE",
        'required_columns': ['ll97_covered_estimated'],
        'recommended_sort': {'by': 'value', 'order': 'desc'},
        'how_to_use': [
            'A low Energy Star score or high site EUI on the profile means bigger fines and an easier sell.',
            'Fines recur annually — frame retrofit cost against cumulative penalties.',
            'Coverage here is estimated by size; confirm against DOB\'s covered-buildings list before quoting.',
        ],
    },
]

_PLAYS_BY_ID = {p['id']: p for p in PLAYS}


def public_play(play):
    """The play as sent to the frontend (everything except the SQL)."""
    return {k: v for k, v in play.items() if k not in ('where', 'required_columns')}


def available_plays(existing_columns):
    """Plays whose required columns exist in this database."""
    return [p for p in PLAYS
            if all(col in existing_columns for col in p['required_columns'])]


def get_play(play_id, existing_columns):
    """The play if it exists AND its columns are available, else None."""
    play = _PLAYS_BY_ID.get(play_id)
    if not play:
        return None
    if not all(col in existing_columns for col in play['required_columns']):
        return None
    return play
