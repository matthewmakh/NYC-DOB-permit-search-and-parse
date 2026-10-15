#!/usr/bin/env python3
"""Normalized import support for non-core sales-intelligence sources.

The three feeds intentionally keep different grains:

* Electrical Details: many scope/floor/item rows per electrical filing.
* Elevator Applications: one DOB filing row that can become a project stage.
* City Record: a public notice or procurement signal, not a DOB permit.
"""

import html
import json
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from project_intelligence import build_project_key


ENDPOINTS = {
    "electrical_details": "https://data.cityofnewyork.us/resource/xmmq-y7za.json",
    "elevator_applications": "https://data.cityofnewyork.us/resource/kfp4-dz4h.json",
    "city_record": "https://data.cityofnewyork.us/resource/dg92-zbpx.json",
}


ELECTRICAL_DETAIL_COLUMNS = [
    "unique_id", "work_id", "floor_id", "sign_id", "job_filing_number",
    "normalized_filing_number", "project_key", "work_description", "item",
    "item_quantity", "item_cost", "fee_amount", "item_detail", "floor_name",
    "from_floor", "to_floor", "floor_detail", "floor_fixtures",
    "floor_ac_receptacles", "floor_att_receptacles", "floor_switches",
    "floor_outlets", "floor_motors_generators", "floor_hpkw", "floor_heaters",
    "floor_kw", "floor_transformers", "floor_kva", "sign_dimensions",
    "sign_sq_footage", "sign_circuits", "sign_lamps", "sign_lamp_wattage",
    "sign_transformers", "sign_va_per_transformer", "sign_total_watts_va",
    "sign_total_aw_guage", "sign_sockets_per_circuit", "sign_materials_guage",
    "sign_text", "sign_manufacturer", "sign_manufacturer_address",
]


ELEVATOR_COLUMNS = [
    "permit_no", "job_type", "issue_date", "exp_date", "bin", "address",
    "applicant", "block", "lot", "status", "filing_date", "work_description",
    "job_number", "bbl", "latitude", "longitude", "borough", "house_number",
    "street_name", "zip_code", "community_board", "bldg_type", "stories",
    "work_type", "permit_status", "filing_status", "permit_type",
    "owner_business_name", "owner_first_name", "owner_last_name",
    "owner_business_type", "owner_street_name", "owner_city", "owner_state",
    "owner_zip_code", "permittee_license_number", "council_district",
    "census_tract", "nta_name", "api_source", "api_last_updated", "project_key",
    "initial_cost", "current_status_date", "applicant_first_name",
    "applicant_last_name", "applicant_business_name", "participant_role",
    "participant_role_confidence", "design_professional_first_name",
    "design_professional_last_name", "design_professional_business_name",
    "design_professional_license", "related_job_number", "elevator_device_type",
    "elevator_work_type", "elevator_building_code",
    "elevator_total_construction_floors", "elevator_review_type_ppn",
    "elevator_electrical_permit_number",
]


CITY_RECORD_COLUMNS = [
    "source", "source_record_id", "signal_type", "title", "description",
    "agency_name", "category", "selection_method", "section_name", "pin",
    "notice_date", "end_date", "due_date", "event_date", "contact_name",
    "contact_phone", "contact_email", "vendor_name", "vendor_address",
    "contract_amount", "building_name", "street_address_1", "street_address_2",
    "city", "state", "zip_code", "source_url", "relevance_score",
    "relevance_reasons", "raw_payload",
]


def _session(app_token: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    if app_token:
        session.headers["X-App-Token"] = app_token
    return session


def _text(value: Any, length: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:length] if length else cleaned


def _number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> Optional[int]:
    number = _number(value)
    return int(number) if number is not None else None


def _date(value: Any) -> Optional[date]:
    parsed = _datetime(value)
    return parsed.date() if parsed else None


def _datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> Optional[bool]:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() in {"yes", "true", "1", "y"}


def _owner(value: Any) -> Optional[str]:
    cleaned = _text(value)
    if not cleaned or cleaned.upper() in {"N/A", "NA", "NOT APPLICABLE", "NONE", "-"}:
        return None
    return cleaned


def _bbl(record: Dict[str, Any]) -> Optional[str]:
    raw = str(record.get("bbl") or "").strip()
    if len(raw) == 10 and raw.isdigit() and raw != "0000000000":
        return raw
    boroughs = {
        "MANHATTAN": "1", "BRONX": "2", "BROOKLYN": "3",
        "QUEENS": "4", "STATEN ISLAND": "5",
    }
    borough = boroughs.get(str(record.get("borough") or "").upper())
    block = str(record.get("block") or "").strip()
    lot = str(record.get("lot") or "").strip()
    if borough and block.isdigit() and lot.isdigit():
        return f"{borough}{int(block):05d}{int(lot):04d}"
    return None


def normalize_electrical_filing_number(value: Any) -> Optional[str]:
    filing = _text(value, 100)
    if not filing:
        return None
    return re.sub(r"-EL$", "", filing, flags=re.IGNORECASE)


class ElectricalDetailsClient:
    SELECT_FIELDS = [column for column in ELECTRICAL_DETAIL_COLUMNS
                     if column not in {"normalized_filing_number", "project_key"}]

    def __init__(self, app_token: Optional[str] = None):
        self.session = _session(app_token)
        self.base_url = ENDPOINTS["electrical_details"]

    def fetch_for_filings(self, filing_numbers: Iterable[str], limit: int = 50000,
                          chunk_size: int = 60) -> List[Dict[str, Any]]:
        variants = set()
        for value in filing_numbers:
            raw = _text(value, 100)
            normalized = normalize_electrical_filing_number(value)
            if not normalized:
                continue
            variants.add(raw)
            variants.add(normalized)
            variants.add(f"{normalized}-EL")
        values = sorted(value for value in variants if value)
        records = []
        for start in range(0, len(values), chunk_size):
            chunk = values[start:start + chunk_size]
            quoted = ",".join("'" + value.replace("'", "''") + "'" for value in chunk)
            offset = 0
            while True:
                params = {
                    "$select": ",".join(self.SELECT_FIELDS),
                    "$where": f"job_filing_number in ({quoted})",
                    "$order": "job_filing_number, unique_id",
                    "$limit": limit,
                    "$offset": offset,
                }
                response = self.session.get(self.base_url, params=params, timeout=60)
                response.raise_for_status()
                page = response.json()
                records.extend(page)
                if len(page) < limit:
                    break
                offset += limit
        return records


class ElevatorApplicationsClient:
    SELECT_FIELDS = [
        "job_filing_number", "job_number", "filing_number", "filing_date",
        "filing_type", "elevatordevicetype", "filing_status",
        "filingstatus_or_filingincludes", "building_code",
        "electrical_permit_number", "bin", "house_number", "street_name", "zip",
        "borough", "block", "lot", "building_type", "buildingstories",
        "associatedjobnumber", "total_construction_floor",
        "plan_examiner_assigned_date", "first_objection_date", "last_objection_date",
        "resubmission_date", "permit_entire_date", "signedoff_date",
        "applicant_firstname", "applicant_lastname", "applicant_businessname",
        "applicant_license_number", "designprofessional_firstname",
        "designprofessional_lastname", "designprofessional",
        "designprofessional_license", "owner_firstname", "owner_lastname",
        "owner_businessname", "owner_address", "owner_city", "owner_state",
        "owner_zip", "owner_type", "descriptionofwork", "estimated_cost",
        "review_type_ppn", "permit_expiration_date", "latitude", "longitude",
        "community_district_number", "city_council_district", "census_tract",
        "bbl", "nta_name",
    ]

    def __init__(self, app_token: Optional[str] = None):
        self.session = _session(app_token)
        self.base_url = ENDPOINTS["elevator_applications"]

    def fetch_applications(self, start_date: str, end_date: str,
                           borough: Optional[str] = None, limit: int = 50000,
                           offset: int = 0) -> List[Dict[str, Any]]:
        where = [
            f"filing_date >= '{start_date}T00:00:00'",
            f"filing_date <= '{end_date}T23:59:59'",
        ]
        if borough:
            where.append("upper(borough)='" + str(borough).upper().replace("'", "''") + "'")
        params = {
            "$select": ",".join(self.SELECT_FIELDS),
            "$where": " AND ".join(where),
            "$order": "filing_date DESC, job_filing_number ASC",
            "$limit": limit,
            "$offset": offset,
        }
        response = self.session.get(self.base_url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()


class CityRecordClient:
    SELECT_FIELDS = [
        "request_id", "start_date", "end_date", "agency_name",
        "type_of_notice_description", "category_description", "short_title",
        "selection_method_description", "section_name",
        "special_case_reason_description", "pin", "due_date", "address_to_request",
        "contact_name", "contact_phone", "email", "contract_amount", "contact_fax",
        "additional_description_1", "additional_description_2",
        "additional_description_3", "other_info_1", "other_info_2", "other_info_3",
        "vendor_name", "vendor_address", "printout_1", "printout_2", "printout_3",
        "document_links", "event_date", "building_name", "street_address_1",
        "street_address_2", "city", "state", "zip_code",
    ]

    def __init__(self, app_token: Optional[str] = None):
        self.session = _session(app_token)
        self.base_url = ENDPOINTS["city_record"]

    def fetch_notices(self, start_date: str, end_date: str, limit: int = 50000,
                      offset: int = 0) -> List[Dict[str, Any]]:
        params = {
            "$select": ",".join(self.SELECT_FIELDS),
            "$where": (
                f"start_date >= '{start_date}T00:00:00' AND "
                f"start_date <= '{end_date}T23:59:59' AND "
                "section_name in ('Procurement','Contract Award Hearings')"
            ),
            "$order": "start_date DESC, request_id ASC",
            "$limit": limit,
            "$offset": offset,
        }
        response = self.session.get(self.base_url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()


def prepare_electrical_detail_rows(records: List[Dict[str, Any]]) -> Tuple[List[tuple], int]:
    rows = []
    skipped = 0
    for record in records:
        unique_id = _text(record.get("unique_id"))
        filing = _text(record.get("job_filing_number"), 100)
        normalized = normalize_electrical_filing_number(filing)
        if not unique_id or not filing or not normalized:
            skipped += 1
            continue
        project_key = build_project_key("dob_now_electrical", normalized, normalized)
        rows.append((
            unique_id, _text(record.get("work_id")), _text(record.get("floor_id")),
            _text(record.get("sign_id")), filing, normalized, project_key,
            _text(record.get("work_description")), _text(record.get("item")),
            _number(record.get("item_quantity")), _number(record.get("item_cost")),
            _number(record.get("fee_amount")), _text(record.get("item_detail")),
            _text(record.get("floor_name")), _text(record.get("from_floor")),
            _text(record.get("to_floor")), _text(record.get("floor_detail")),
            _integer(record.get("floor_fixtures")),
            _integer(record.get("floor_ac_receptacles")),
            _integer(record.get("floor_att_receptacles")),
            _integer(record.get("floor_switches")), _integer(record.get("floor_outlets")),
            _integer(record.get("floor_motors_generators")),
            _number(record.get("floor_hpkw")), _integer(record.get("floor_heaters")),
            _number(record.get("floor_kw")), _integer(record.get("floor_transformers")),
            _number(record.get("floor_kva")), _text(record.get("sign_dimensions")),
            _number(record.get("sign_sq_footage")), _integer(record.get("sign_circuits")),
            _integer(record.get("sign_lamps")), _number(record.get("sign_lamp_wattage")),
            _integer(record.get("sign_transformers")),
            _number(record.get("sign_va_per_transformer")),
            _number(record.get("sign_total_watts_va")),
            _text(record.get("sign_total_aw_guage")),
            _integer(record.get("sign_sockets_per_circuit")),
            _text(record.get("sign_materials_guage")), _text(record.get("sign_text")),
            _text(record.get("sign_manufacturer")),
            _text(record.get("sign_manufacturer_address")),
        ))
    deduped = {row[0]: row for row in rows}
    return list(deduped.values()), skipped + len(rows) - len(deduped)


def prepare_elevator_rows(records: List[Dict[str, Any]]) -> Tuple[List[tuple], int]:
    rows = []
    skipped = 0
    now = datetime.now()
    for record in records:
        filing = _text(record.get("job_filing_number"), 100)
        job_number = _text(record.get("job_number"), 50)
        if not filing or not job_number:
            skipped += 1
            continue
        milestones = [
            _datetime(record.get(field)) for field in (
                "signedoff_date", "permit_entire_date", "resubmission_date",
                "last_objection_date", "first_objection_date",
                "plan_examiner_assigned_date", "filing_date",
            )
        ]
        milestones = [value for value in milestones if value]
        status_date = max(milestones) if milestones else None
        address = " ".join(filter(None, [
            _text(record.get("house_number")), _text(record.get("street_name"))
        ])) or None
        applicant_name = " ".join(filter(None, [
            _text(record.get("applicant_firstname")),
            _text(record.get("applicant_lastname")),
        ])) or None
        device = _text(record.get("elevatordevicetype"), 100)
        elevator_work = _text(record.get("filingstatus_or_filingincludes"), 100)
        rows.append((
            f"VT:{filing}",
            _text(" / ".join(filter(None, ["Elevator", device, elevator_work])), 500),
            _date(record.get("permit_entire_date")),
            _date(record.get("permit_expiration_date")),
            _text(record.get("bin"), 50), address,
            _text(record.get("applicant_businessname") or applicant_name, 225),
            _text(record.get("block"), 20), _text(record.get("lot"), 20),
            _text(record.get("filing_status"), 50), _date(record.get("filing_date")),
            _text(record.get("descriptionofwork")), job_number, _bbl(record),
            _number(record.get("latitude")), _number(record.get("longitude")),
            _text(record.get("borough"), 20), _text(record.get("house_number"), 50),
            _text(record.get("street_name"), 255), _text(record.get("zip"), 15),
            _text(record.get("community_district_number"), 3),
            _text(record.get("building_type"), 50), _text(record.get("buildingstories"), 20),
            "ELEVATOR", _text(record.get("filing_status"), 50),
            _text(record.get("filing_status"), 50), _text(record.get("filing_type"), 50),
            _text(_owner(record.get("owner_businessname")), 255),
            _text(record.get("owner_firstname"), 100),
            _text(record.get("owner_lastname"), 100), _text(record.get("owner_type"), 100),
            _text(record.get("owner_address"), 255), _text(record.get("owner_city"), 100),
            _text(record.get("owner_state"), 20), _text(record.get("owner_zip"), 15),
            _text(record.get("applicant_license_number"), 50),
            _text(record.get("city_council_district"), 20),
            _text(record.get("census_tract"), 20), _text(record.get("nta_name"), 255),
            "dob_now_elevator", now,
            build_project_key("dob_now_elevator", job_number, filing),
            _number(record.get("estimated_cost")), status_date,
            _text(record.get("applicant_firstname"), 100),
            _text(record.get("applicant_lastname"), 100),
            _text(record.get("applicant_businessname"), 255),
            "elevator_applicant", 1.0,
            _text(record.get("designprofessional_firstname"), 100),
            _text(record.get("designprofessional_lastname"), 100),
            _text(record.get("designprofessional"), 255),
            _text(record.get("designprofessional_license"), 100),
            _text(record.get("associatedjobnumber"), 50), device, elevator_work,
            _text(record.get("building_code"), 20),
            _integer(record.get("total_construction_floor")),
            _bool(record.get("review_type_ppn")),
            _text(record.get("electrical_permit_number"), 100),
        ))
    deduped = {row[0]: row for row in rows}
    return list(deduped.values()), skipped + len(rows) - len(deduped)


_RELEVANCE_TERMS = {
    "access control": 35, "security system": 35, "surveillance": 35,
    "closed circuit television": 35, "cctv": 35, "intercom": 35,
    "structured cabling": 35, "low voltage": 35, "audio visual": 30,
    "audiovisual": 30, "public address": 25, "network infrastructure": 30,
    "telecommunication": 25, "wi-fi": 25, "wireless network": 25,
    "building automation": 35, "lighting control": 30, "smart building": 35,
    "electrical": 20, "elevator": 20, "security": 20, "camera": 25,
    "fiber optic": 30, "fire alarm": 20, "modernization": 12,
    "renovation": 10, "construction": 8, "capital improvement": 10,
}


def _plain_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _city_record_relevance(record: Dict[str, Any]) -> Tuple[int, List[str]]:
    haystack = " ".join(_plain_text(record.get(field)).lower() for field in (
        "short_title", "category_description", "additional_description_1",
        "additional_description_2", "additional_description_3", "other_info_1",
        "other_info_2", "other_info_3", "printout_1", "printout_2", "printout_3",
    ))
    matched = [(term, weight) for term, weight in _RELEVANCE_TERMS.items()
               if term in haystack]
    score = min(100, sum(weight for _, weight in matched))
    reasons = [term for term, _ in sorted(matched, key=lambda item: -item[1])]
    notice_type = str(record.get("type_of_notice_description") or "").lower()
    if any(term in notice_type for term in ("solicitation", "intent to award", "request")):
        score = min(100, score + 10)
        reasons.append("active procurement notice")
    return score, reasons


def prepare_city_record_rows(records: List[Dict[str, Any]]) -> Tuple[List[tuple], int]:
    rows = []
    skipped = 0
    for record in records:
        request_id = _text(record.get("request_id"), 100)
        title = _plain_text(record.get("short_title"))
        if not request_id or not title:
            skipped += 1
            continue
        score, reasons = _city_record_relevance(record)
        description = "\n\n".join(filter(None, [
            _plain_text(record.get(field)) for field in (
                "additional_description_1", "additional_description_2",
                "additional_description_3", "other_info_1", "other_info_2",
                "other_info_3",
            )
        ])) or None
        rows.append((
            "city_record", request_id,
            _text(record.get("type_of_notice_description"), 100), title, description,
            _text(record.get("agency_name"), 255),
            _text(record.get("category_description"), 255),
            _text(record.get("selection_method_description"), 255),
            _text(record.get("section_name"), 255), _text(record.get("pin"), 100),
            _date(record.get("start_date")), _date(record.get("end_date")),
            _datetime(record.get("due_date")), _datetime(record.get("event_date")),
            _text(record.get("contact_name"), 255),
            _text(record.get("contact_phone"), 100), _text(record.get("email"), 255),
            _text(record.get("vendor_name"), 255), _text(record.get("vendor_address")),
            _number(record.get("contract_amount")),
            _text(record.get("building_name"), 255),
            _text(record.get("street_address_1")), _text(record.get("street_address_2")),
            _text(record.get("city"), 100), _text(record.get("state"), 50),
            _text(record.get("zip_code"), 20),
            f"https://data.cityofnewyork.us/resource/dg92-zbpx.json?request_id={request_id}",
            score, json.dumps(reasons), json.dumps(record),
        ))
    deduped = {row[1]: row for row in rows}
    return list(deduped.values()), skipped + len(rows) - len(deduped)
