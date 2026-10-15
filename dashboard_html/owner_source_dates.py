"""Dates belonging to owner evidence, distinct from when we fetched it."""
from datetime import date, datetime
import re


def source_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = str(value or '').strip()
    for pattern, length in (('%Y-%m-%d', 10), ('%Y%m%d', 8), ('%m/%d/%Y', 10)):
        try:
            return datetime.strptime(value[:length], pattern).date()
        except ValueError:
            continue
    return None


def latest_hpd_registration(rows):
    # EndDate is an expiration date, not when the owner was last reported.
    if len(rows) > 1 and not any(source_date(row.get('lastregistrationdate')) for row in rows):
        raise ValueError('HPD processed dates are unavailable; cannot select newest registration')
    return max(rows, key=lambda row: (
        source_date(row.get('lastregistrationdate')) or date.min,
        int(row.get('registrationid') or 0)), default=None)


def latest_rpad_record(rows, year_column):
    def key(row):
        year = re.match(r'^\d{4}', str(row.get(year_column) or ''))
        period = str(row.get('period') or '').strip().upper()
        # A final roll supersedes a tentative roll for the same fiscal year.
        return (int(year[0]) if year else 0,
                {'TENTATIVE': 1, 'FINAL': 2}.get(period, 0),
                str(row.get('bble') or ''))
    dated = [row for row in rows if key(row)[0]]
    if rows and not dated:
        raise ValueError('RPAD assessment years are invalid; cannot select newest owner')
    return max(dated, key=key, default=None)


def owner_source_dates(building, refresh_states=()):
    states = {row['source']: row for row in refresh_states}
    fields = building.get('owner_source_fields') or building

    def entry(source, value=None, label='Last reported', checked=None, period=None, note=None):
        state = states.get(source, {})
        reported = source_date(value)
        checked = state.get('checked_at') or checked
        return dict(reported_date=reported.isoformat() if reported else None,
                    date_label=label, period=period, note=note,
                    checked_at=checked.isoformat() if hasattr(checked, 'isoformat') else checked,
                    refresh_failed=bool(state.get('error')))

    year = fields.get('rpad_assessment_year')
    period = fields.get('rpad_assessment_period')
    rpad_period = f"FY {year}" + (f" · {period.title()}" if period else '') if year else None
    version = fields.get('pluto_version')
    return {
        'acris': entry('acris', building.get('sale_recorded_date'), 'Deed recorded',
                       building.get('acris_last_enriched')),
        'pluto': entry('pluto', period=f'PLUTO {version}' if version else None,
                       note='PLUTO publishes a dataset version, not an owner-report date.'),
        'rpad': entry('rpad', period=rpad_period,
                      note='Historical assessment roll; the fiscal year is not a current ownership report.'),
        'hpd': entry('hpd', fields.get('hpd_last_registration_date'), 'Registration processed'),
        'ecb': entry('ecb', fields.get('ecb_respondent_issue_date'), 'Violation issued',
                     fields.get('ecb_last_checked'),
                     note='Respondent on the latest named violation; not necessarily the owner.'),
        'sos': entry('sos', checked=building.get('sos_last_enriched'),
                     note='The SOS response does not provide a report date for this contact. Formation date is not a contact-update date.'),
    }
