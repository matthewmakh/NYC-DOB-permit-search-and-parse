'use client'

import { useState, useEffect, useCallback } from 'react'
import type { PropertyCardData, PropertiesFilters, PropertiesStats, PaginatedResponse } from '@/types'
import { createSearchParams } from '@/lib/utils'

interface UsePropertiesResult {
  properties: PropertyCardData[]
  stats: PropertiesStats | null
  pagination: {
    page: number
    per_page: number
    total: number
    total_pages: number
  } | null
  isLoading: boolean
  error: string | null
  refetch: () => void
}

export function useProperties(filters: PropertiesFilters): UsePropertiesResult {
  const [properties, setProperties] = useState<PropertyCardData[]>([])
  const [stats, setStats] = useState<PropertiesStats | null>(null)
  const [pagination, setPagination] = useState<UsePropertiesResult['pagination']>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchProperties = useCallback(async () => {
    setIsLoading(true)
    setError(null)

    try {
      const params = createSearchParams({
        search: filters.search,
        owner: filters.owner,
        borough: filters.borough,
        propertyType: filters.propertyType,
        buildingClass: filters.buildingClass,
        minValue: filters.minValue,
        maxValue: filters.maxValue,
        minSalePrice: filters.minSalePrice,
        maxSalePrice: filters.maxSalePrice,
        saleDateFrom: filters.saleDateFrom,
        saleDateTo: filters.saleDateTo,
        minUnits: filters.minUnits,
        maxUnits: filters.maxUnits,
        withPermits: filters.withPermits,
        minPermits: filters.minPermits,
        permitType: filters.permitType,
        recentPermitDays: filters.recentPermitDays,
        financingMin: filters.financingMin,
        financingMax: filters.financingMax,
        cashOnly: filters.cashOnly,
        hasViolations: filters.hasViolations,
        hasEnrichableOwner: filters.hasEnrichableOwner,
        sortBy: filters.sortBy,
        sortOrder: filters.sortOrder,
        page: filters.page,
        perPage: filters.perPage,
      })

      const response = await fetch(`/api/properties?${params.toString()}`)
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const data = await response.json()

      if (data.success) {
        setProperties(data.data || [])
        setPagination(data.pagination || null)
        setStats(data.stats || null)
      } else {
        throw new Error(data.error || 'Failed to fetch properties')
      }
    } catch (err) {
      console.error('Error fetching properties:', err)
      setError(err instanceof Error ? err.message : 'Failed to load properties')
      setProperties([])
    } finally {
      setIsLoading(false)
    }
  }, [filters])

  useEffect(() => {
    fetchProperties()
  }, [fetchProperties])

  return {
    properties,
    stats,
    pagination,
    isLoading,
    error,
    refetch: fetchProperties,
  }
}
