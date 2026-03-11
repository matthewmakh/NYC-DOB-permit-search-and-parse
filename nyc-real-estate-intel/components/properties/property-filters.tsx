'use client'

import { useState, useEffect, useCallback } from 'react'
import { Search, User, DollarSign, Calendar, Building2, HardHat, Percent } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { debounce } from '@/lib/utils'
import type { PropertiesFilters } from '@/types'

interface PropertyFiltersProps {
  filters: PropertiesFilters
  onChange: (filters: Partial<PropertiesFilters>) => void
}

export function PropertyFilters({ filters, onChange }: PropertyFiltersProps) {
  // Local state for debounced inputs
  const [searchValue, setSearchValue] = useState(filters.search || '')
  const [ownerValue, setOwnerValue] = useState(filters.owner || '')

  // Debounced search handler
  const debouncedSearch = useCallback(
    debounce((value: string) => {
      onChange({ search: value })
    }, 300),
    [onChange]
  )

  // Debounced owner handler
  const debouncedOwner = useCallback(
    debounce((value: string) => {
      onChange({ owner: value })
    }, 300),
    [onChange]
  )

  // Update local state when filters change externally
  useEffect(() => {
    setSearchValue(filters.search || '')
    setOwnerValue(filters.owner || '')
  }, [filters.search, filters.owner])

  return (
    <div className="space-y-6">
      {/* Universal Search */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <Search className="h-3.5 w-3.5" />
          Universal Search
        </Label>
        <Input
          placeholder="Address, BBL, Owner, ZIP..."
          value={searchValue}
          onChange={(e) => {
            setSearchValue(e.target.value)
            debouncedSearch(e.target.value)
          }}
        />
      </div>

      {/* Owner Search */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <User className="h-3.5 w-3.5" />
          Owner Search
        </Label>
        <Input
          placeholder="Search by owner name..."
          value={ownerValue}
          onChange={(e) => {
            setOwnerValue(e.target.value)
            debouncedOwner(e.target.value)
          }}
        />
        <p className="text-xs text-muted-foreground">Click property to see full portfolio</p>
      </div>

      {/* Assessed Value */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <DollarSign className="h-3.5 w-3.5" />
          Assessed Value
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <Input
            type="number"
            placeholder="Min ($)"
            value={filters.minValue || ''}
            onChange={(e) => onChange({ minValue: e.target.value ? Number(e.target.value) : undefined })}
          />
          <Input
            type="number"
            placeholder="Max ($)"
            value={filters.maxValue || ''}
            onChange={(e) => onChange({ maxValue: e.target.value ? Number(e.target.value) : undefined })}
          />
        </div>
      </div>

      {/* Sale Price */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <DollarSign className="h-3.5 w-3.5" />
          Sale Price
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <Input
            type="number"
            placeholder="Min ($)"
            value={filters.minSalePrice || ''}
            onChange={(e) => onChange({ minSalePrice: e.target.value ? Number(e.target.value) : undefined })}
          />
          <Input
            type="number"
            placeholder="Max ($)"
            value={filters.maxSalePrice || ''}
            onChange={(e) => onChange({ maxSalePrice: e.target.value ? Number(e.target.value) : undefined })}
          />
        </div>
      </div>

      {/* Sale Date */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <Calendar className="h-3.5 w-3.5" />
          Sale Date
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <Input
            type="date"
            value={filters.saleDateFrom || ''}
            onChange={(e) => onChange({ saleDateFrom: e.target.value || undefined })}
          />
          <Input
            type="date"
            value={filters.saleDateTo || ''}
            onChange={(e) => onChange({ saleDateTo: e.target.value || undefined })}
          />
        </div>
      </div>

      {/* Borough */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          🗺️ Borough
        </Label>
        <Select
          value={filters.borough || 'all'}
          onValueChange={(value) => onChange({ borough: value === 'all' ? '' : value })}
        >
          <SelectTrigger>
            <SelectValue placeholder="All Boroughs" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Boroughs</SelectItem>
            <SelectItem value="1">Manhattan</SelectItem>
            <SelectItem value="2">Bronx</SelectItem>
            <SelectItem value="3">Brooklyn</SelectItem>
            <SelectItem value="4">Queens</SelectItem>
            <SelectItem value="5">Staten Island</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Property Type */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <Building2 className="h-3.5 w-3.5" />
          Property Type
        </Label>
        <Select
          value={filters.propertyType || 'all'}
          onValueChange={(value) => onChange({ propertyType: value === 'all' ? '' : value as PropertiesFilters['propertyType'] })}
        >
          <SelectTrigger>
            <SelectValue placeholder="All Types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="residential">Residential</SelectItem>
            <SelectItem value="commercial">Commercial</SelectItem>
            <SelectItem value="mixed">Mixed Use</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Building Class */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          🏢 Building Class
        </Label>
        <Input
          placeholder="e.g., A, B, C, D"
          value={filters.buildingClass || ''}
          onChange={(e) => onChange({ buildingClass: e.target.value || undefined })}
        />
        <p className="text-xs text-muted-foreground">NYC building classification code</p>
      </div>

      {/* Units */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          🏠 Units
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <Input
            type="number"
            placeholder="Min"
            value={filters.minUnits || ''}
            onChange={(e) => onChange({ minUnits: e.target.value ? Number(e.target.value) : undefined })}
          />
          <Input
            type="number"
            placeholder="Max"
            value={filters.maxUnits || ''}
            onChange={(e) => onChange({ maxUnits: e.target.value ? Number(e.target.value) : undefined })}
          />
        </div>
      </div>

      {/* Permit Activity */}
      <div className="space-y-3">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <HardHat className="h-3.5 w-3.5" />
          Permit Activity
        </Label>
        <div className="flex items-center space-x-2">
          <Checkbox
            id="withPermits"
            checked={filters.withPermits}
            onCheckedChange={(checked) => onChange({ withPermits: checked === true })}
          />
          <label
            htmlFor="withPermits"
            className="text-sm cursor-pointer"
          >
            Only properties with permits
          </label>
        </div>
        <Input
          type="number"
          placeholder="Min permit count"
          value={filters.minPermits || ''}
          onChange={(e) => onChange({ minPermits: e.target.value ? Number(e.target.value) : undefined })}
        />
        <Select
          value={filters.permitType || 'all'}
          onValueChange={(value) => onChange({ permitType: value === 'all' ? '' : value })}
        >
          <SelectTrigger>
            <SelectValue placeholder="All Permit Types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="NB">NB - New Building</SelectItem>
            <SelectItem value="AL">AL - Alteration</SelectItem>
            <SelectItem value="DM">DM - Demolition</SelectItem>
            <SelectItem value="EW">EW - Equipment Work</SelectItem>
            <SelectItem value="PL">PL - Plumbing</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={filters.recentPermitDays ? String(filters.recentPermitDays) : 'any'}
          onValueChange={(value) => onChange({ recentPermitDays: value === 'any' ? undefined : Number(value) })}
        >
          <SelectTrigger>
            <SelectValue placeholder="Any time" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">Any time</SelectItem>
            <SelectItem value="7">Last week</SelectItem>
            <SelectItem value="30">Last 30 days</SelectItem>
            <SelectItem value="90">Last 90 days</SelectItem>
            <SelectItem value="180">Last 6 months</SelectItem>
            <SelectItem value="365">Last year</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Financing */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
          <Percent className="h-3.5 w-3.5" />
          Financing Ratio
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <Input
            type="number"
            placeholder="Min (%)"
            min="0"
            max="100"
            value={filters.financingMin || ''}
            onChange={(e) => onChange({ financingMin: e.target.value ? Number(e.target.value) : undefined })}
          />
          <Input
            type="number"
            placeholder="Max (%)"
            min="0"
            max="100"
            value={filters.financingMax || ''}
            onChange={(e) => onChange({ financingMax: e.target.value ? Number(e.target.value) : undefined })}
          />
        </div>
      </div>

      {/* Cash Only */}
      <div className="flex items-center space-x-2">
        <Checkbox
          id="cashOnly"
          checked={filters.cashOnly}
          onCheckedChange={(checked) => onChange({ cashOnly: checked === true })}
        />
        <label
          htmlFor="cashOnly"
          className="text-sm cursor-pointer"
        >
          💵 Cash purchases only
        </label>
      </div>

      {/* Has Violations */}
      <div className="space-y-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          ⚠️ HPD Violations
        </Label>
        <Select
          value={filters.hasViolations === null ? 'any' : filters.hasViolations ? 'true' : 'false'}
          onValueChange={(value) => onChange({ 
            hasViolations: value === 'any' ? null : value === 'true' 
          })}
        >
          <SelectTrigger>
            <SelectValue placeholder="Any" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">Any</SelectItem>
            <SelectItem value="true">Has violations</SelectItem>
            <SelectItem value="false">No violations</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Enrichable Owner */}
      <div className="flex items-center space-x-2">
        <Checkbox
          id="hasEnrichableOwner"
          checked={filters.hasEnrichableOwner}
          onCheckedChange={(checked) => onChange({ hasEnrichableOwner: checked === true })}
        />
        <label
          htmlFor="hasEnrichableOwner"
          className="text-sm cursor-pointer"
        >
          📞 Has enrichable owner
        </label>
      </div>
      <p className="text-xs text-muted-foreground -mt-4">
        Properties with a person name (not LLC) that can be looked up
      </p>
    </div>
  )
}
