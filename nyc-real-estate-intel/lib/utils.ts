import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { BOROUGH_CODES } from '@/types'

/**
 * Merge Tailwind CSS classes with clsx
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Format currency with proper formatting
 */
export function formatCurrency(value: number | null | undefined, options?: {
  compact?: boolean
  decimals?: number
}): string {
  if (value === null || value === undefined) return 'N/A'
  
  const { compact = false, decimals = 0 } = options || {}
  
  if (compact && value >= 1000000) {
    return `$${(value / 1000000).toFixed(1)}M`
  }
  
  if (compact && value >= 1000) {
    return `$${(value / 1000).toFixed(0)}K`
  }
  
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

/**
 * Format number with commas
 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'N/A'
  return new Intl.NumberFormat('en-US').format(value)
}

/**
 * Format date to readable string
 */
export function formatDate(date: Date | string | null | undefined, options?: {
  format?: 'short' | 'long' | 'relative'
}): string {
  if (!date) return 'N/A'
  
  const d = typeof date === 'string' ? new Date(date) : date
  const { format = 'short' } = options || {}
  
  if (format === 'relative') {
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    const days = Math.floor(diff / (1000 * 60 * 60 * 24))
    
    if (days === 0) return 'Today'
    if (days === 1) return 'Yesterday'
    if (days < 7) return `${days} days ago`
    if (days < 30) return `${Math.floor(days / 7)} weeks ago`
    if (days < 365) return `${Math.floor(days / 30)} months ago`
    return `${Math.floor(days / 365)} years ago`
  }
  
  if (format === 'long') {
    return d.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    })
  }
  
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/**
 * Get borough name from borough code
 */
export function getBoroughName(code: number | null | undefined): string {
  if (!code) return 'Unknown'
  return BOROUGH_CODES[code] || 'Unknown'
}

/**
 * Parse BBL string into components
 */
export function parseBBL(bbl: string): { borough: number; block: number; lot: number } | null {
  if (!bbl || bbl.length !== 10) return null
  
  const borough = parseInt(bbl.substring(0, 1), 10)
  const block = parseInt(bbl.substring(1, 6), 10)
  const lot = parseInt(bbl.substring(6, 10), 10)
  
  if (isNaN(borough) || isNaN(block) || isNaN(lot)) return null
  
  return { borough, block, lot }
}

/**
 * Format BBL for display (e.g., "1-00234-0056")
 */
export function formatBBL(bbl: string): string {
  const parsed = parseBBL(bbl)
  if (!parsed) return bbl
  
  return `${parsed.borough}-${parsed.block.toString().padStart(5, '0')}-${parsed.lot.toString().padStart(4, '0')}`
}

/**
 * Check if a string looks like a BBL
 */
export function isBBLFormat(str: string): boolean {
  // Match formats: "1001230056", "1-00123-0056", "1 00123 0056"
  const cleaned = str.replace(/[-\s]/g, '')
  return /^\d{10}$/.test(cleaned)
}

/**
 * Clean BBL string (remove dashes/spaces)
 */
export function cleanBBL(str: string): string {
  return str.replace(/[-\s]/g, '')
}

/**
 * Truncate text with ellipsis
 */
export function truncate(str: string | null | undefined, length: number): string {
  if (!str) return ''
  if (str.length <= length) return str
  return str.slice(0, length).trim() + '...'
}

/**
 * Calculate financing ratio
 */
export function calculateFinancingRatio(
  mortgageAmount: number | null | undefined,
  salePrice: number | null | undefined
): number | null {
  if (!mortgageAmount || !salePrice || salePrice === 0) return null
  return Math.round((mortgageAmount / salePrice) * 100)
}

/**
 * Check if name looks like a business/entity vs. a person
 */
export function isBusinessName(name: string | null | undefined): boolean {
  if (!name) return false
  
  const upperName = name.toUpperCase()
  const businessIndicators = [
    'LLC', 'INC', 'CORP', 'LTD', 'CO', 'LP', 'LLP', 'PLLC', 'PC',
    'COMPANY', 'PROPERTIES', 'REALTY', 'HOLDINGS', 'ENTERPRISES',
    'INVESTMENTS', 'DEVELOPMENT', 'MANAGEMENT', 'FOUNDATION',
    'TRUST', 'ESTATE', 'ASSOCIATES', 'PARTNERS', 'PARTNERSHIP',
    'GROUP', 'CAPITAL', 'FUND', 'HOUSING', 'AUTHORITY', 'BANK',
  ]
  
  const words = upperName.split(/\s+/)
  return words.some(word => businessIndicators.includes(word))
}

/**
 * Debounce function
 */
export function debounce<T extends (...args: Parameters<T>) => void>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null
  
  return (...args: Parameters<T>) => {
    if (timeout) clearTimeout(timeout)
    timeout = setTimeout(() => func(...args), wait)
  }
}

/**
 * Sleep/delay function
 */
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

/**
 * Create URL search params from object, filtering out empty values
 */
export function createSearchParams(params: Record<string, unknown>): URLSearchParams {
  const searchParams = new URLSearchParams()
  
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.set(key, String(value))
    }
  })
  
  return searchParams
}

/**
 * Safe JSON parse with fallback
 */
export function safeJsonParse<T>(json: string, fallback: T): T {
  try {
    return JSON.parse(json) as T
  } catch {
    return fallback
  }
}

/**
 * Generate a random ID
 */
export function generateId(): string {
  return Math.random().toString(36).substring(2, 15)
}
