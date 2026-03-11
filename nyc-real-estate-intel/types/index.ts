// ============================================================================
// CORE DATA TYPES
// NYC Real Estate Intelligence Platform
// ============================================================================

// ============================================================================
// DATABASE ENTITIES
// ============================================================================

export interface Building {
  id: number
  bbl: string
  address: string | null
  borough: number | null
  block: number | null
  lot: number | null
  
  // PLUTO Data
  current_owner_name: string | null
  units: number | null
  sqft: number | null
  year_built: number | null
  year_altered: number | null
  building_class: string | null
  land_use: string | null
  residential_units: number | null
  total_units: number | null
  num_floors: number | null
  building_sqft: number | null
  lot_sqft: number | null
  
  // RPAD Data
  owner_name_rpad: string | null
  assessed_land_value: number | null
  assessed_total_value: number | null
  
  // HPD Data
  owner_name_hpd: string | null
  hpd_violations_count: number | null
  hpd_complaints_count: number | null
  
  // ACRIS Data - Primary Deed
  sale_price: number | null
  sale_date: Date | string | null
  sale_recorded_date: Date | string | null
  sale_buyer_primary: string | null
  sale_seller_primary: string | null
  sale_percent_transferred: number | null
  sale_crfn: string | null
  purchase_date: Date | string | null
  purchase_price: number | null
  
  // ACRIS Data - Primary Mortgage
  mortgage_amount: number | null
  mortgage_date: Date | string | null
  mortgage_lender_primary: string | null
  mortgage_crfn: string | null
  
  // Calculated Intelligence
  is_cash_purchase: boolean | null
  financing_ratio: number | null
  days_since_sale: number | null
  
  // Transaction Counts
  acris_total_transactions: number | null
  acris_deed_count: number | null
  acris_mortgage_count: number | null
  acris_satisfaction_count: number | null
  acris_last_enriched: Date | string | null
  
  // Tax Delinquency & Liens
  has_tax_delinquency: boolean | null
  tax_delinquency_count: number | null
  tax_delinquency_water_only: boolean | null
  ecb_violation_count: number | null
  ecb_total_balance: number | null
  ecb_open_violations: number | null
  ecb_total_penalty: number | null
  ecb_amount_paid: number | null
  ecb_most_recent_hearing_date: Date | string | null
  ecb_most_recent_hearing_status: string | null
  ecb_respondent_name: string | null
  ecb_respondent_address: string | null
  ecb_respondent_city: string | null
  
  // Timestamps
  created_at: Date | string | null
  updated_at: Date | string | null
  
  // Relations (optional - populated in some queries)
  permits?: Permit[]
  permit_count?: number
}

export interface Permit {
  id: number
  permit_no: string | null
  job_type: string | null
  address: string | null
  borough: number | null
  block: string | null
  lot: string | null
  bbl: string | null
  applicant: string | null
  applicant_license: string | null
  stories: number | null
  total_units: number | null
  use_type: string | null
  issue_date: Date | string | null
  expiration_date: Date | string | null
  permit_status: string | null
  link: string | null
  
  // Contact Info
  permittee_business_name: string | null
  permittee_phone: string | null
  owner_business_name: string | null
  owner_phone: string | null
  superintendent_business_name: string | null
  site_safety_mgr_business_name: string | null
  
  // Geocoding
  latitude: number | null
  longitude: number | null
  
  // Relations
  building_id: number | null
  created_at: Date | string | null
}

export interface User {
  id: number
  email: string
  password_hash: string
  is_admin: boolean
  is_verified: boolean
  subscription_status: 'none' | 'active' | 'canceled' | 'past_due'
  stripe_customer_id: string | null
  stripe_subscription_id: string | null
  created_at: Date | string
  updated_at: Date | string
}

export interface Session {
  id: number
  user_id: number
  session_token: string
  ip_address: string | null
  user_agent: string | null
  expires_at: Date | string
  created_at: Date | string
}

// ============================================================================
// API REQUEST/RESPONSE TYPES
// ============================================================================

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  message?: string
}

export interface PaginatedResponse<T> {
  success: boolean
  data: T[]
  pagination: {
    page: number
    per_page: number
    total: number
    total_pages: number
  }
}

// ============================================================================
// PROPERTIES PAGE TYPES
// ============================================================================

export interface PropertiesFilters {
  search?: string
  owner?: string
  borough?: string
  propertyType?: 'residential' | 'commercial' | 'mixed' | ''
  buildingClass?: string
  minValue?: number
  maxValue?: number
  minSalePrice?: number
  maxSalePrice?: number
  saleDateFrom?: string
  saleDateTo?: string
  minUnits?: number
  maxUnits?: number
  withPermits?: boolean
  minPermits?: number
  permitType?: string
  recentPermitDays?: number
  financingMin?: number
  financingMax?: number
  cashOnly?: boolean
  hasViolations?: boolean | null
  hasEnrichableOwner?: boolean
  sortBy?: 'sale_date' | 'value' | 'sale_price' | 'address' | 'owner' | 'permits' | 'units'
  sortOrder?: 'asc' | 'desc'
  page?: number
  perPage?: number
}

export interface PropertyCardData {
  id: number
  bbl: string
  address: string | null
  borough_name: string
  current_owner_name: string | null
  building_class: string | null
  total_units: number | null
  num_floors: number | null
  year_built: number | null
  assessed_total_value: number | null
  sale_price: number | null
  sale_date: Date | string | null
  is_cash_purchase: boolean | null
  permit_count: number
  has_violations: boolean
  is_enrichable: boolean
}

export interface PropertiesStats {
  total: number
  totalValue: number
  cashPurchases: number
  recentSales: number
}

// ============================================================================
// SEARCH TYPES
// ============================================================================

export interface SearchResult {
  type: 'property' | 'owner' | 'address'
  bbl?: string
  address?: string
  owner_name?: string
  match_field: string
}

export interface SearchSuggestion {
  type: 'address' | 'bbl' | 'owner'
  value: string
  display: string
  bbl?: string
}

// ============================================================================
// MARKET STATS TYPES
// ============================================================================

export interface MarketStats {
  totalPermits: number
  recentSales: number
  totalProperties: number
  qualifiedLeads: number
  totalContacts: number
  buildingsWithOwners: number
  enrichmentRate: number
}

// ============================================================================
// BUILDING PROFILE TYPES
// ============================================================================

export interface BuildingProfile extends Building {
  permits: Permit[]
  transactions: AcrisDocument[]
  violations: Violation[]
  risk_score: number
  risk_factors: RiskFactor[]
  owner_sources: OwnerSource[]
}

export interface AcrisDocument {
  id: number
  bbl: string
  document_id: string
  crfn: string | null
  doc_type: string | null
  doc_date: Date | string | null
  recorded_date: Date | string | null
  doc_amount: number | null
  parties: AcrisParty[]
}

export interface AcrisParty {
  id: number
  document_id: number
  party_type: 'BUYER' | 'SELLER' | 'LENDER' | 'BORROWER' | string
  name: string
  address: string | null
}

export interface Violation {
  id: number
  bbl: string
  violation_id: string
  type: 'HPD' | 'DOB' | 'ECB'
  class: string | null
  status: string | null
  description: string | null
  issue_date: Date | string | null
  disposition_date: Date | string | null
  penalty_amount: number | null
}

export interface RiskFactor {
  category: string
  factor: string
  score: number
  description: string
}

export interface OwnerSource {
  source: 'PLUTO' | 'RPAD' | 'HPD' | 'ACRIS' | 'ECB'
  name: string
  date?: Date | string | null
}

// ============================================================================
// ENRICHMENT TYPES
// ============================================================================

export interface EnrichmentResult {
  id: number
  building_id: number
  owner_name: string
  source: 'enformion' | 'apify'
  phones: string[]
  emails: string[]
  addresses: EnrichmentAddress[]
  raw_response: Record<string, unknown>
  created_at: Date | string
}

export interface EnrichmentAddress {
  street: string
  city: string
  state: string
  zip: string
  type?: 'current' | 'previous'
}

export interface BulkEnrichmentEstimate {
  total_properties: number
  enrichable_count: number
  already_enriched: number
  total_cost: number
  per_unit_cost: number
}

// ============================================================================
// CONTRACTOR TYPES
// ============================================================================

export interface Contractor {
  name: string
  license_number: string | null
  permit_count: number
  total_units: number
  boroughs: string[]
  job_types: string[]
  recent_permits: Permit[]
}

// ============================================================================
// ACTIVITY LOGGING TYPES
// ============================================================================

export interface ActivityLog {
  id: number
  user_id: number | null
  activity_type: string
  category: string
  description: string | null
  metadata: Record<string, unknown> | null
  ip_address: string | null
  user_agent: string | null
  created_at: Date | string
}

export type ActivityType = 
  | 'page_view'
  | 'search'
  | 'export'
  | 'login'
  | 'logout'
  | 'signup'
  | 'enrichment'
  | 'error'

// ============================================================================
// UTILITY TYPES
// ============================================================================

export type Borough = 'Manhattan' | 'Bronx' | 'Brooklyn' | 'Queens' | 'Staten Island'

export const BOROUGH_CODES: Record<number, Borough> = {
  1: 'Manhattan',
  2: 'Bronx',
  3: 'Brooklyn',
  4: 'Queens',
  5: 'Staten Island',
}

export const BOROUGH_NUMBERS: Record<Borough, number> = {
  'Manhattan': 1,
  'Bronx': 2,
  'Brooklyn': 3,
  'Queens': 4,
  'Staten Island': 5,
}

export const JOB_TYPES: Record<string, string> = {
  'NB': 'New Building',
  'A1': 'Major Alteration',
  'A2': 'Minor Alteration',
  'A3': 'Minor Alteration',
  'AL': 'Alteration',
  'DM': 'Demolition',
  'EW': 'Equipment Work',
  'PL': 'Plumbing',
  'EQ': 'Equipment',
  'SG': 'Sign',
  'FO': 'Foundation',
}
