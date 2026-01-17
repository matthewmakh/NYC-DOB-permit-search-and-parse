#!/usr/bin/env python3
"""
NY Secretary of State Business Lookup - Generic Reusable Module

A standalone module for looking up business entity information from the
New York Department of State. Returns clean dataclasses with owner/agent info.

No database dependencies. No ORM. Just API calls and clean data.

Usage:
    from ny_sos_lookup import lookup_business, lookup_businesses
    
    # Single lookup
    result = lookup_business("ABC LLC")
    if result.found:
        for person in result.people:
            print(f"{person.title}: {person.full_name}")
    
    # Batch lookup
    results = lookup_businesses(["ABC LLC", "XYZ CORP"])
    for name, result in results.items():
        print(f"{name}: {'Found' if result.found else 'Not Found'}")

Author: Matthew Makh
"""

import asyncio
import re
import random
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple

try:
    import httpx
except ImportError:
    raise ImportError("httpx is required. Install with: pip install httpx")

log = logging.getLogger(__name__)


# ============================================================================
# DATA CLASSES - Clean Output Types
# ============================================================================

@dataclass
class SOSPerson:
    """A person associated with a business (CEO, Agent, etc.)"""
    full_name: str
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    title: str = ""  # "CEO", "Registered Agent", "Service of Process Agent"
    street: str = ""
    city: str = ""
    state: str = ""
    zipcode: str = ""
    
    def has_address(self) -> bool:
        """Check if this person has a usable address."""
        return bool(self.street or self.city or self.zipcode)
    
    def full_address(self) -> str:
        """Get formatted full address."""
        parts = [p for p in [self.street, self.city, self.state, self.zipcode] if p]
        return ", ".join(parts)


@dataclass
class SOSBusinessResult:
    """Result of a business lookup."""
    # Search info
    query_name: str           # What you searched for
    normalized_name: str      # Cleaned/normalized version
    found: bool = False       # Did we find it?
    error: str = ""           # Error message if failed
    
    # Business details (if found)
    dos_id: str = ""          # NY DOS ID
    entity_name: str = ""     # Official registered name
    entity_type: str = ""     # "LimitedLiabilityCompany", "Corporation", etc.
    status: str = ""          # "Active", "Inactive", etc.
    jurisdiction: str = ""    # "New York", "Delaware", etc.
    formation_date: Optional[datetime] = None
    county: str = ""
    
    # The people you want!
    people: List[SOSPerson] = field(default_factory=list)
    
    # Raw data for debugging
    raw_response: dict = field(default_factory=dict)
    
    def get_ceo(self) -> Optional[SOSPerson]:
        """Get the CEO if present."""
        for p in self.people:
            if p.title == "CEO":
                return p
        return None
    
    def get_registered_agent(self) -> Optional[SOSPerson]:
        """Get the Registered Agent if present."""
        for p in self.people:
            if p.title == "Registered Agent":
                return p
        return None
    
    def get_individuals(self) -> List[SOSPerson]:
        """Get only people who appear to be individuals (not companies)."""
        return [p for p in self.people if _is_person_name(p.full_name)]


# ============================================================================
# NAME NORMALIZATION UTILITIES
# ============================================================================

def normalize_business_name(name: str) -> str:
    """
    Normalize business name for deduplication and caching.
    """
    if not name:
        return ""
    
    name = name.upper().strip()
    
    # Remove location suffix
    name = re.sub(r'\s*-\s*[A-Z\s]+,\s*[A-Z]{2}(\s+\d{5})?$', '', name, flags=re.IGNORECASE)
    
    # Remove common business suffixes
    suffixes = [
        r'\bLLC\b', r'\bL\.L\.C\.', r'\bINC\b', r'\bINC\.', r'\bINCORPORATED\b',
        r'\bCORP\b', r'\bCORP\.', r'\bCORPORATION\b', r'\bLTD\b', r'\bLTD\.',
        r'\bLIMITED\b', r'\bLP\b', r'\bL\.P\.', r'\bLLP\b', r'\bL\.L\.P\.',
        r'\bPC\b', r'\bP\.C\.', r'\bPLLC\b', r'\bP\.L\.L\.C\.', r'\bCO\b',
        r'\bCO\.', r'\bCOMPANY\b', r'\bDBA\b', r'\bD/B/A\b', r'\bD\.B\.A\.',
        r'\bUSA\b', r'\bU\.S\.A\.'
    ]
    
    for suffix in suffixes:
        name = re.sub(suffix, '', name)
    
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    
    return name.strip()


def is_likely_individual(name: str) -> bool:
    """Check if a name looks like an individual person rather than a corporation."""
    if not name:
        return False
    
    name_upper = name.upper()
    
    corp_indicators = [
        'LLC', 'L.L.C.', 'INC', 'INC.', 'CORP', 'CORP.', 'CORPORATION',
        'LTD', 'LTD.', 'LIMITED', 'LP', 'L.P.', 'LLP', 'L.L.P.',
        'COMPANY', 'CO.', 'GROUP', 'SERVICES', 'ASSOCIATES', 'PARTNERS',
        'ENTERPRISES', 'HOLDINGS', 'MANAGEMENT', 'CONSULTING', 'AGENCY',
        'TRUST', 'FUND', 'BANK', 'FOUNDATION', 'INSTITUTE', 'PC', 'P.C.',
        'PLLC', 'P.L.L.C.', 'DBA', 'D/B/A'
    ]
    
    for indicator in corp_indicators:
        if indicator in name_upper:
            return False
    
    words = name.split()
    if len(words) < 2 or len(words) > 4:
        return False
    
    if any(char.isdigit() for char in name):
        return False
    
    return True


def _is_person_name(name: str) -> bool:
    """Check if a name looks like a person vs a company."""
    if not name:
        return False
    name_upper = name.upper()
    company_indicators = [
        'LLC', 'INC', 'CORP', 'CORPORATION', 'LTD', 'LIMITED', 'LP', 'LLP',
        'COMPANY', 'GROUP', 'SERVICES', 'ASSOCIATES', 'PARTNERS',
        'TRUST', 'FUND', 'BANK', 'FOUNDATION'
    ]
    for indicator in company_indicators:
        if indicator in name_upper:
            return False
    words = name.split()
    return 2 <= len(words) <= 5


def _parse_name(full_name: str) -> Tuple[str, str, str]:
    """Parse a full name into (first, middle, last) components."""
    if not full_name:
        return ('', '', '')
    parts = full_name.strip().split()
    if len(parts) == 1:
        return (parts[0], '', '')
    elif len(parts) == 2:
        return (parts[0], '', parts[1])
    else:
        return (parts[0], ' '.join(parts[1:-1]), parts[-1])


def _clean_business_name_for_search(name: str) -> str:
    """Clean business name for API searching."""
    if not name:
        return ""
    name = re.sub(r'\s*-\s*[A-Z\s]+,\s*[A-Z]{2}(\s+\d{5})?$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+(USA|U\.S\.A\.)$', '', name, flags=re.IGNORECASE)
    return name.strip()


def _parse_formation_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse formation date with multiple format support."""
    if not date_str:
        return None
    
    formats = ['%Y-%m-%d', '%m/%d/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%m-%d-%Y']
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    return None


# ============================================================================
# ASYNC NY SOS API CLIENT
# ============================================================================

class AsyncNYSOSClient:
    """Async client for NY Department of State business lookup API."""
    
    BASE_URL = "https://apps.dos.ny.gov/PublicInquiryWeb/api/PublicInquiry"
    
    def __init__(self, concurrency: int = 5, timeout: int = 30, max_retries: int = 3):
        self.concurrency = concurrency
        self.timeout = timeout
        self.max_retries = max_retries
        self.semaphore = asyncio.Semaphore(concurrency)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/json',
            'Origin': 'https://apps.dos.ny.gov',
            'Referer': 'https://apps.dos.ny.gov/publicInquiry/',
        }
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        self._client = httpx.AsyncClient(headers=self.headers)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
    
    async def lookup(self, business_name: str) -> SOSBusinessResult:
        """Look up a single business by name."""
        normalized = normalize_business_name(business_name)
        
        result = SOSBusinessResult(
            query_name=business_name,
            normalized_name=normalized,
        )
        
        if not business_name or not normalized:
            result.error = "Empty business name"
            return result
        
        async with self.semaphore:
            for attempt in range(self.max_retries):
                try:
                    matches = await self._search_business(business_name)
                    
                    if not matches:
                        return result
                    
                    active_matches = [m for m in matches if m.get('entity_status') == 'Active']
                    selected = active_matches[0] if active_matches else matches[0]
                    
                    details = await self._get_business_details(
                        selected['dos_id'], 
                        selected['entity_name']
                    )
                    
                    if not details:
                        return result
                    
                    result.found = True
                    result.dos_id = details.get('dos_id', '')
                    result.entity_name = details.get('entity_name', '')
                    result.entity_type = details.get('entity_type', '')
                    result.status = details.get('status', '')
                    result.jurisdiction = details.get('jurisdiction', '')
                    result.formation_date = _parse_formation_date(details.get('formation_date'))
                    result.county = details.get('county', '')
                    result.people = details.get('people', [])
                    result.raw_response = details.get('raw_response', {})
                    
                    return result
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (429, 503):
                        sleep_time = (2 ** attempt) + random.random()
                        await asyncio.sleep(sleep_time)
                        continue
                    result.error = f"HTTP {e.response.status_code}"
                    return result
                    
                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep((2 ** attempt) + random.random())
                        continue
                    result.error = f"Connection error: {e}"
                    return result
                    
                except Exception as e:
                    result.error = str(e)
                    return result
            
            result.error = "Max retries exceeded"
            return result
    
    async def lookup_many(self, business_names: List[str]) -> Dict[str, SOSBusinessResult]:
        """Look up multiple businesses concurrently."""
        tasks = [self.lookup(name) for name in business_names]
        results = await asyncio.gather(*tasks)
        return {r.query_name: r for r in results}
    
    async def _search_business(self, business_name: str) -> List[Dict]:
        """Search for a business by name."""
        search_name = _clean_business_name_for_search(business_name)
        if not search_name:
            return []
        
        json_data = {
            'searchValue': search_name,
            'searchByTypeIndicator': 'EntityName',
            'searchExpressionIndicator': 'BeginsWith',
            'entityStatusIndicator': 'AllStatuses',
            'entityTypeIndicator': [
                'Corporation',
                'LimitedLiabilityCompany',
                'LimitedPartnership',
                'LimitedLiabilityPartnership',
            ],
            'listPaginationInfo': {
                'listStartRecord': 1,
                'listEndRecord': 50,
            },
        }
        
        response = await self._client.post(
            f"{self.BASE_URL}/GetComplexSearchMatchingEntities",
            json=json_data,
            timeout=self.timeout
        )
        response.raise_for_status()
        content = response.json()
        
        results = []
        for result in content.get('entitySearchResultList', []):
            results.append({
                'dos_id': result.get('dosID'),
                'entity_name': result.get('entityName'),
                'entity_status': result.get('entityStatus'),
                'entity_type': result.get('entityType'),
                'jurisdiction': result.get('jurisdiction'),
                'formation_date': result.get('formationDate'),
            })
        
        return results
    
    async def _get_business_details(self, dos_id: str, entity_name: str) -> Optional[Dict]:
        """Get detailed business information including owners."""
        json_data = {
            'SearchID': dos_id,
            'EntityName': entity_name,
            'AssumedNameFlag': 'false',
        }
        
        response = await self._client.post(
            f"{self.BASE_URL}/GetEntityRecordByID",
            json=json_data,
            timeout=self.timeout
        )
        response.raise_for_status()
        content = response.json()
        
        # Extract people (CEO, agents)
        people = []
        titles = {
            'ceo': 'CEO',
            'sopAddress': 'Service of Process Agent',
            'registeredAgent': 'Registered Agent'
        }
        
        for key, title in titles.items():
            person_data = content.get(key, {})
            if person_data and person_data.get('name'):
                name = person_data.get('name', '')
                first_name, middle_name, last_name = _parse_name(name)
                address = person_data.get('address', {})
                
                people.append(SOSPerson(
                    full_name=name,
                    first_name=first_name,
                    middle_name=middle_name,
                    last_name=last_name,
                    title=title,
                    street=address.get('streetAddress', ''),
                    city=address.get('city', ''),
                    state=address.get('state', ''),
                    zipcode=address.get('zipCode', ''),
                ))
        
        # Entity details are nested under entityGeneralInfo
        entity_info = content.get('entityGeneralInfo', {})
        
        return {
            'dos_id': dos_id,
            'entity_name': entity_info.get('entityName'),
            'entity_type': entity_info.get('entityType'),
            'status': entity_info.get('entityStatus'),
            'jurisdiction': entity_info.get('jurisdiction'),
            'formation_date': entity_info.get('dateOfInitialDosFiling') or entity_info.get('effectiveDateInitialFiling'),
            'county': entity_info.get('county'),
            'people': people,
            'raw_response': content,
        }


# ============================================================================
# SIMPLE SYNC WRAPPERS
# ============================================================================

def lookup_business(business_name: str, timeout: int = 30) -> SOSBusinessResult:
    """
    Look up a single business by name (synchronous).
    
    Example:
        result = lookup_business("ABC LLC")
        if result.found:
            ceo = result.get_ceo()
            if ceo:
                print(f"CEO: {ceo.full_name}, {ceo.city}, {ceo.state}")
    """
    async def _lookup():
        async with AsyncNYSOSClient(timeout=timeout) as client:
            return await client.lookup(business_name)
    
    return asyncio.run(_lookup())


def lookup_businesses(
    business_names: List[str], 
    concurrency: int = 5,
    timeout: int = 30
) -> Dict[str, SOSBusinessResult]:
    """
    Look up multiple businesses by name (synchronous, but internally async).
    
    Example:
        results = lookup_businesses(["ABC LLC", "XYZ CORP"])
        for name, result in results.items():
            if result.found:
                print(f"{name}: {result.status}")
    """
    async def _lookup_many():
        async with AsyncNYSOSClient(concurrency=concurrency, timeout=timeout) as client:
            return await client.lookup_many(business_names)
    
    return asyncio.run(_lookup_many())


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) < 2:
        print("Usage: python ny_sos_lookup.py 'BUSINESS NAME LLC'")
        print("\nExample:")
        print("  python ny_sos_lookup.py 'ACME HOLDINGS LLC'")
        sys.exit(1)
    
    business_name = " ".join(sys.argv[1:])
    print(f"\nLooking up: {business_name}")
    print("=" * 60)
    
    result = lookup_business(business_name)
    
    if result.found:
        print(f"✓ Found: {result.entity_name}")
        print(f"  DOS ID: {result.dos_id}")
        print(f"  Type: {result.entity_type}")
        print(f"  Status: {result.status}")
        print(f"  Jurisdiction: {result.jurisdiction}")
        if result.formation_date:
            print(f"  Formed: {result.formation_date.strftime('%Y-%m-%d')}")
        if result.county:
            print(f"  County: {result.county}")
        
        print(f"\n  People ({len(result.people)}):")
        for person in result.people:
            print(f"    {person.title}: {person.full_name}")
            if person.has_address():
                print(f"      Address: {person.full_address()}")
        
        individuals = result.get_individuals()
        if individuals:
            print(f"\n  Individuals only ({len(individuals)}):")
            for person in individuals:
                print(f"    {person.title}: {person.full_name}")
    else:
        print(f"✗ Not found")
        if result.error:
            print(f"  Error: {result.error}")
