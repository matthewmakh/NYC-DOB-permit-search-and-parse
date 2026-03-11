'use client'

import { useState, useCallback, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { 
  Search, 
  Download, 
  Filter, 
  ChevronDown,
  Building2,
  DollarSign,
  Calendar,
  Users,
  X,
  Loader2
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { PropertyCard } from '@/components/properties/property-card'
import { PropertyFilters } from '@/components/properties/property-filters'
import { useProperties } from '@/hooks/use-properties'
import { formatCurrency, formatNumber } from '@/lib/utils'
import type { PropertiesFilters } from '@/types'

// Loading fallback for Suspense
function PropertiesLoading() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  )
}

// Inner component that uses useSearchParams
function PropertiesContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [showFilters, setShowFilters] = useState(true)
  
  // Parse initial filters from URL
  const initialFilters: PropertiesFilters = {
    search: searchParams.get('search') || '',
    owner: searchParams.get('owner') || '',
    borough: searchParams.get('borough') || '',
    propertyType: (searchParams.get('propertyType') as PropertiesFilters['propertyType']) || '',
    minValue: searchParams.get('minValue') ? Number(searchParams.get('minValue')) : undefined,
    maxValue: searchParams.get('maxValue') ? Number(searchParams.get('maxValue')) : undefined,
    minUnits: searchParams.get('minUnits') ? Number(searchParams.get('minUnits')) : undefined,
    maxUnits: searchParams.get('maxUnits') ? Number(searchParams.get('maxUnits')) : undefined,
    withPermits: searchParams.get('withPermits') === 'true',
    cashOnly: searchParams.get('cashOnly') === 'true',
    sortBy: (searchParams.get('sortBy') as PropertiesFilters['sortBy']) || 'sale_date',
    sortOrder: (searchParams.get('sortOrder') as PropertiesFilters['sortOrder']) || 'desc',
    page: searchParams.get('page') ? Number(searchParams.get('page')) : 1,
    perPage: searchParams.get('perPage') ? Number(searchParams.get('perPage')) : 50,
  }

  const [filters, setFilters] = useState<PropertiesFilters>(initialFilters)
  const { properties, stats, pagination, isLoading, error } = useProperties(filters)

  // Update URL when filters change
  const updateFilters = useCallback((newFilters: Partial<PropertiesFilters>) => {
    const updated = { ...filters, ...newFilters, page: 1 } // Reset page on filter change
    setFilters(updated)
    
    // Update URL params
    const params = new URLSearchParams()
    Object.entries(updated).forEach(([key, value]) => {
      if (value !== undefined && value !== '' && value !== false) {
        params.set(key, String(value))
      }
    })
    router.push(`/properties?${params.toString()}`, { scroll: false })
  }, [filters, router])

  const clearFilters = () => {
    const cleared: PropertiesFilters = {
      sortBy: 'sale_date',
      sortOrder: 'desc',
      page: 1,
      perPage: 50,
    }
    setFilters(cleared)
    router.push('/properties', { scroll: false })
  }

  const handlePageChange = (page: number) => {
    const updated = { ...filters, page }
    setFilters(updated)
    const params = new URLSearchParams()
    Object.entries(updated).forEach(([key, value]) => {
      if (value !== undefined && value !== '' && value !== false) {
        params.set(key, String(value))
      }
    })
    router.push(`/properties?${params.toString()}`, { scroll: false })
  }

  const activeFilterCount = Object.entries(filters).filter(
    ([key, value]) => 
      value !== undefined && 
      value !== '' && 
      value !== false &&
      !['sortBy', 'sortOrder', 'page', 'perPage'].includes(key)
  ).length

  return (
    <div className="flex min-h-screen">
      {/* Sidebar Filters */}
      {showFilters && (
        <aside className="w-80 border-r bg-card p-6 overflow-y-auto max-h-[calc(100vh-64px)] sticky top-16">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Filter className="h-5 w-5" />
              Advanced Filters
            </h2>
            {activeFilterCount > 0 && (
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                Clear All
              </Button>
            )}
          </div>
          <PropertyFilters 
            filters={filters} 
            onChange={updateFilters} 
          />
        </aside>
      )}

      {/* Main Content */}
      <main className="flex-1 p-6">
        {/* Stats Dashboard */}
        <section className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <StatCard 
            icon={Building2} 
            label="Total Properties" 
            value={pagination?.total} 
            loading={isLoading} 
          />
          <StatCard 
            icon={DollarSign} 
            label="Total Value" 
            value={stats?.totalValue} 
            format="currency"
            loading={isLoading} 
          />
          <StatCard 
            icon={Calendar} 
            label="Cash Purchases" 
            value={stats?.cashPurchases} 
            loading={isLoading} 
          />
          <StatCard 
            icon={Users} 
            label="Recent Sales (90d)" 
            value={stats?.recentSales} 
            loading={isLoading} 
          />
        </section>

        {/* Controls Bar */}
        <div className="flex items-center justify-between mb-6 flex-wrap gap-4">
          <div className="flex items-center gap-4">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowFilters(!showFilters)}
            >
              <Filter className="h-4 w-4 mr-2" />
              {showFilters ? 'Hide' : 'Show'} Filters
              {activeFilterCount > 0 && (
                <Badge variant="secondary" className="ml-2">
                  {activeFilterCount}
                </Badge>
              )}
            </Button>
            <span className="text-sm text-muted-foreground">
              {isLoading ? (
                <Skeleton className="h-4 w-24 inline-block" />
              ) : (
                `${formatNumber(pagination?.total || 0)} properties`
              )}
            </span>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Sort by:</span>
              <Select
                value={filters.sortBy}
                onValueChange={(value) => updateFilters({ sortBy: value as PropertiesFilters['sortBy'] })}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="sale_date">Sale Date</SelectItem>
                  <SelectItem value="value">Assessed Value</SelectItem>
                  <SelectItem value="sale_price">Sale Price</SelectItem>
                  <SelectItem value="address">Address</SelectItem>
                  <SelectItem value="owner">Owner</SelectItem>
                  <SelectItem value="permits">Permits</SelectItem>
                  <SelectItem value="units">Units</SelectItem>
                </SelectContent>
              </Select>
              <Select
                value={filters.sortOrder}
                onValueChange={(value) => updateFilters({ sortOrder: value as PropertiesFilters['sortOrder'] })}
              >
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="desc">High to Low</SelectItem>
                  <SelectItem value="asc">Low to High</SelectItem>
                </SelectContent>
              </Select>
            </div>
            
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Per page:</span>
              <Select
                value={String(filters.perPage)}
                onValueChange={(value) => updateFilters({ perPage: Number(value) })}
              >
                <SelectTrigger className="w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="25">25</SelectItem>
                  <SelectItem value="50">50</SelectItem>
                  <SelectItem value="100">100</SelectItem>
                  <SelectItem value="200">200</SelectItem>
                </SelectContent>
              </Select>
            </div>
            
            <Button variant="outline" size="sm">
              <Download className="h-4 w-4 mr-2" />
              Export
            </Button>
          </div>
        </div>

        {/* Error State */}
        {error && (
          <div className="p-4 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive mb-6">
            {error}
          </div>
        )}

        {/* Properties Grid */}
        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 9 }).map((_, i) => (
              <PropertyCardSkeleton key={i} />
            ))}
          </div>
        ) : properties.length === 0 ? (
          <div className="text-center py-16">
            <Search className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
            <h3 className="text-lg font-medium mb-2">No properties found</h3>
            <p className="text-muted-foreground mb-4">
              Try adjusting your filters or search criteria
            </p>
            <Button variant="outline" onClick={clearFilters}>
              Clear Filters
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {properties.map((property) => (
              <PropertyCard key={property.id} property={property} />
            ))}
          </div>
        )}

        {/* Pagination */}
        {pagination && pagination.total_pages > 1 && (
          <div className="flex items-center justify-center gap-2 mt-8">
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.page === 1}
              onClick={() => handlePageChange(pagination.page - 1)}
            >
              Previous
            </Button>
            <div className="flex items-center gap-1">
              {Array.from({ length: Math.min(5, pagination.total_pages) }, (_, i) => {
                let pageNum: number
                if (pagination.total_pages <= 5) {
                  pageNum = i + 1
                } else if (pagination.page <= 3) {
                  pageNum = i + 1
                } else if (pagination.page >= pagination.total_pages - 2) {
                  pageNum = pagination.total_pages - 4 + i
                } else {
                  pageNum = pagination.page - 2 + i
                }
                return (
                  <Button
                    key={pageNum}
                    variant={pagination.page === pageNum ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => handlePageChange(pageNum)}
                  >
                    {pageNum}
                  </Button>
                )
              })}
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.page === pagination.total_pages}
              onClick={() => handlePageChange(pagination.page + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </main>
    </div>
  )
}

// Page wrapper with Suspense
export default function PropertiesPage() {
  return (
    <Suspense fallback={<PropertiesLoading />}>
      <PropertiesContent />
    </Suspense>
  )
}

// Sub-components
function StatCard({
  icon: Icon,
  label,
  value,
  format,
  loading,
}: {
  icon: React.ElementType
  label: string
  value: number | undefined
  format?: 'currency'
  loading: boolean
}) {
  return (
    <Card>
      <CardContent className="p-4 flex items-center gap-3">
        <div className="p-2 rounded-lg bg-primary/10">
          <Icon className="h-5 w-5 text-primary" />
        </div>
        <div>
          <div className="text-sm text-muted-foreground">{label}</div>
          {loading ? (
            <Skeleton className="h-6 w-20" />
          ) : (
            <div className="text-xl font-bold">
              {format === 'currency' 
                ? formatCurrency(value, { compact: true })
                : formatNumber(value)}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function PropertyCardSkeleton() {
  return (
    <Card>
      <CardContent className="p-4">
        <Skeleton className="h-5 w-3/4 mb-2" />
        <Skeleton className="h-4 w-1/2 mb-4" />
        <div className="grid grid-cols-2 gap-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
        </div>
        <div className="flex gap-2 mt-4">
          <Skeleton className="h-6 w-16" />
          <Skeleton className="h-6 w-16" />
        </div>
      </CardContent>
    </Card>
  )
}
