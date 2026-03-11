'use client'

import { useRouter } from 'next/navigation'
import { Building2, MapPin, Calendar, DollarSign, Home } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { formatCurrency, formatDate, getBoroughName, truncate } from '@/lib/utils'
import type { PropertyCardData } from '@/types'

interface PropertyCardProps {
  property: PropertyCardData
}

export function PropertyCard({ property }: PropertyCardProps) {
  const router = useRouter()
  
  const handleClick = () => {
    router.push(`/properties/${property.bbl}`)
  }

  return (
    <Card 
      className="group cursor-pointer transition-all hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5"
      onClick={handleClick}
    >
      <CardContent className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div>
            <h3 className="font-semibold text-sm line-clamp-1 group-hover:text-primary transition-colors">
              {property.address || 'Address Not Available'}
            </h3>
            <p className="text-xs text-muted-foreground flex items-center gap-1 mt-1">
              <MapPin className="h-3 w-3" />
              {property.borough_name}
            </p>
          </div>
          {property.is_cash_purchase && (
            <Badge variant="success" className="text-xs">
              💵 Cash
            </Badge>
          )}
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-2 gap-y-2 gap-x-4 text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Home className="h-3.5 w-3.5" />
            <span>{property.total_units || 0} units</span>
          </div>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Building2 className="h-3.5 w-3.5" />
            <span>{property.num_floors || 0} floors</span>
          </div>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Calendar className="h-3.5 w-3.5" />
            <span>Built {property.year_built || 'N/A'}</span>
          </div>
          <div className="flex items-center gap-2 text-muted-foreground">
            <DollarSign className="h-3.5 w-3.5" />
            <span>{formatCurrency(property.assessed_total_value, { compact: true })}</span>
          </div>
        </div>

        {/* Owner */}
        {property.current_owner_name && (
          <div className="mt-3 pt-3 border-t">
            <p className="text-xs text-muted-foreground">Owner</p>
            <p className="text-sm font-medium truncate">
              {truncate(property.current_owner_name, 35)}
            </p>
          </div>
        )}

        {/* Sale Info */}
        {property.sale_price && (
          <div className="mt-3 pt-3 border-t flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">Last Sale</p>
              <p className="text-sm font-bold text-green-400">
                {formatCurrency(property.sale_price, { compact: true })}
              </p>
            </div>
            {property.sale_date && (
              <span className="text-xs text-muted-foreground">
                {formatDate(property.sale_date, { format: 'relative' })}
              </span>
            )}
          </div>
        )}

        {/* Badges */}
        <div className="flex flex-wrap gap-1.5 mt-3">
          {property.building_class && (
            <Badge variant="outline" className="text-xs">
              Class {property.building_class}
            </Badge>
          )}
          {property.permit_count > 0 && (
            <Badge variant="secondary" className="text-xs">
              🏗️ {property.permit_count} permits
            </Badge>
          )}
          {property.has_violations && (
            <Badge variant="warning" className="text-xs">
              ⚠️ Violations
            </Badge>
          )}
          {property.is_enrichable && (
            <Badge variant="info" className="text-xs">
              📞 Enrichable
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
