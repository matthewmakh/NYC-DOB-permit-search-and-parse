import { NextRequest, NextResponse } from 'next/server'
import { prisma } from '@/lib/db'
import { Prisma } from '@prisma/client'

export const dynamic = 'force-dynamic'

const BOROUGH_MAP: Record<string, string> = {
  manhattan: '1',
  bronx: '2',
  brooklyn: '3',
  queens: '4',
  'staten island': '5',
  mn: '1',
  bx: '2',
  bk: '3',
  qn: '4',
  si: '5',
}

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams
    
    // Parse query parameters
    const search = searchParams.get('search') || ''
    const owner = searchParams.get('owner') || ''
    const borough = searchParams.get('borough') || ''
    const propertyType = searchParams.get('propertyType') || ''
    const buildingClass = searchParams.get('buildingClass') || ''
    const minValue = searchParams.get('minValue') ? parseInt(searchParams.get('minValue')!) : undefined
    const maxValue = searchParams.get('maxValue') ? parseInt(searchParams.get('maxValue')!) : undefined
    const minSalePrice = searchParams.get('minSalePrice') ? parseInt(searchParams.get('minSalePrice')!) : undefined
    const maxSalePrice = searchParams.get('maxSalePrice') ? parseInt(searchParams.get('maxSalePrice')!) : undefined
    const saleDateFrom = searchParams.get('saleDateFrom') || ''
    const saleDateTo = searchParams.get('saleDateTo') || ''
    const minUnits = searchParams.get('minUnits') ? parseInt(searchParams.get('minUnits')!) : undefined
    const maxUnits = searchParams.get('maxUnits') ? parseInt(searchParams.get('maxUnits')!) : undefined
    const withPermits = searchParams.get('withPermits') === 'true'
    const cashOnly = searchParams.get('cashOnly') === 'true'
    const hasViolations = searchParams.get('hasViolations') === 'true'
    const hasEnrichableOwner = searchParams.get('hasEnrichableOwner') === 'true'
    const sortBy = searchParams.get('sortBy') || 'sale_date'
    const sortOrder = (searchParams.get('sortOrder') || 'desc') as 'asc' | 'desc'
    const page = parseInt(searchParams.get('page') || '1')
    const perPage = Math.min(parseInt(searchParams.get('perPage') || '20'), 100)

    // Build where clause
    const where: Prisma.BuildingWhereInput = {}

    // Universal search (address, BBL, owner name)
    if (search) {
      where.OR = [
        { address: { contains: search, mode: 'insensitive' } },
        { bbl: { contains: search } },
        { current_owner_name: { contains: search, mode: 'insensitive' } },
      ]
    }

    // Owner search
    if (owner) {
      where.current_owner_name = { contains: owner, mode: 'insensitive' }
    }

    // Borough filter (DB stores as varchar like '1', '2', etc. or 'MANHATTAN')
    if (borough) {
      const boroughCode = BOROUGH_MAP[borough.toLowerCase()]
      if (boroughCode) {
        where.borough = boroughCode
      } else {
        where.borough = { contains: borough, mode: 'insensitive' }
      }
    }

    // Property type filter
    if (propertyType) {
      where.building_class = { startsWith: propertyType }
    }

    // Building class filter
    if (buildingClass) {
      where.building_class = buildingClass
    }

    // Value range (use assessed_total_value)
    if (minValue !== undefined || maxValue !== undefined) {
      where.assessed_total_value = {}
      if (minValue !== undefined) where.assessed_total_value.gte = minValue
      if (maxValue !== undefined) where.assessed_total_value.lte = maxValue
    }

    // Sale price range
    if (minSalePrice !== undefined || maxSalePrice !== undefined) {
      where.sale_price = {}
      if (minSalePrice !== undefined) where.sale_price.gte = minSalePrice
      if (maxSalePrice !== undefined) where.sale_price.lte = maxSalePrice
    }

    // Sale date range
    if (saleDateFrom || saleDateTo) {
      where.sale_date = {}
      if (saleDateFrom) where.sale_date.gte = new Date(saleDateFrom)
      if (saleDateTo) where.sale_date.lte = new Date(saleDateTo)
    }

    // Units range
    if (minUnits !== undefined || maxUnits !== undefined) {
      where.total_units = {}
      if (minUnits !== undefined) where.total_units.gte = minUnits
      if (maxUnits !== undefined) where.total_units.lte = maxUnits
    }

    // Cash only purchases
    if (cashOnly) {
      where.is_cash_purchase = true
    }

    // Has violations
    if (hasViolations) {
      where.OR = [
        ...(where.OR || []),
        { hpd_open_violations: { gt: 0 } },
        { ecb_open_violations: { gt: 0 } },
      ]
    }

    // Has enrichable owner (has individual owner, not corporation)
    if (hasEnrichableOwner) {
      where.AND = [
        ...(Array.isArray(where.AND) ? where.AND : where.AND ? [where.AND] : []),
        { current_owner_name: { not: { contains: 'LLC' } } },
        { current_owner_name: { not: { contains: 'CORP' } } },
        { current_owner_name: { not: { contains: 'INC' } } },
      ]
    }

    // Build order by
    type OrderByKey = 'sale_date' | 'sale_price' | 'assessed_total_value' | 'total_units' | 'year_built'
    const orderByMap: Record<string, OrderByKey> = {
      'sale_date': 'sale_date',
      'last_sale_date': 'sale_date',
      'sale_price': 'sale_price',
      'last_sale_price': 'sale_price',
      'value': 'assessed_total_value',
      'assessed_total': 'assessed_total_value',
      'units': 'total_units',
      'units_total': 'total_units',
      'year_built': 'year_built',
    }

    const orderByField = orderByMap[sortBy] || 'sale_date'
    const orderBy: Prisma.BuildingOrderByWithRelationInput = {
      [orderByField]: sortOrder
    }

    // Get total count for pagination
    const total = await prisma.building.count({ where })

    // Get buildings with pagination
    const buildings = await prisma.building.findMany({
      where,
      orderBy,
      skip: (page - 1) * perPage,
      take: perPage,
    })

    // Get permit counts for these buildings
    const bbls = buildings.map(b => b.bbl)
    const permitCounts = await prisma.permit.groupBy({
      by: ['bbl'],
      where: {
        bbl: { in: bbls }
      },
      _count: { id: true }
    })
    const permitCountMap = new Map(permitCounts.map(p => [p.bbl, p._count.id]))

    // Filter by permits if needed (post-query filtering)
    let filteredBuildings = buildings
    
    if (withPermits) {
      filteredBuildings = filteredBuildings.filter(b => (permitCountMap.get(b.bbl) || 0) > 0)
    }

    // Transform to PropertyCardData
    const properties = filteredBuildings.map(building => ({
      id: building.id,
      bbl: building.bbl,
      address: building.address || 'Unknown Address',
      owner_name: building.current_owner_name,
      borough: building.borough,
      building_class: building.building_class,
      assessed_value: building.assessed_total_value ? Number(building.assessed_total_value) : null,
      last_sale_price: building.sale_price ? Number(building.sale_price) : null,
      last_sale_date: building.sale_date?.toISOString() || null,
      units_total: building.total_units,
      units_residential: building.residential_units,
      year_built: building.year_built,
      lot_area: building.lot_sqft,
      building_area: building.building_sqft,
      mortgage_amount: building.mortgage_amount ? Number(building.mortgage_amount) : null,
      mortgage_date: building.mortgage_date?.toISOString() || null,
      violations_count: (building.hpd_open_violations || 0) + (building.ecb_open_violations || 0),
      permit_count: permitCountMap.get(building.bbl) || 0,
      has_contact: false, // Will be enriched later
      is_enriched: !!building.acris_last_enriched,
    }))

    // Calculate stats
    const stats = {
      totalProperties: total,
      totalValue: properties.reduce((sum, p) => sum + (p.assessed_value || 0), 0),
      cashPurchases: buildings.filter(b => b.is_cash_purchase).length,
      recentSales: properties.filter(p => {
        if (!p.last_sale_date) return false
        const saleDate = new Date(p.last_sale_date)
        const thirtyDaysAgo = new Date()
        thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30)
        return saleDate >= thirtyDaysAgo
      }).length,
    }

    return NextResponse.json({
      success: true,
      data: properties,
      pagination: {
        page,
        per_page: perPage,
        total,
        total_pages: Math.ceil(total / perPage),
      },
      stats,
    })
  } catch (error) {
    console.error('Properties API error:', error)
    return NextResponse.json(
      { success: false, error: 'Failed to fetch properties' },
      { status: 500 }
    )
  }
}
