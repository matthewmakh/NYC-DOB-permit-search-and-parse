import { NextResponse } from 'next/server'
import prisma from '@/lib/db'
import type { MarketStats } from '@/types'

export const dynamic = 'force-dynamic'
export const revalidate = 60 // Cache for 1 minute

export async function GET() {
  try {
    // Run all queries in parallel for better performance
    const [
      totalPermits,
      permitsWithContacts,
      totalBuildings,
      buildingsWithOwners,
      recentSales,
    ] = await Promise.all([
      // Total permits
      prisma.permit.count(),
      
      // Permits with contacts (has phone number)
      prisma.permit.count({
        where: {
          OR: [
            { permittee_phone: { not: null } },
            { owner_phone: { not: null } },
          ],
        },
      }),
      
      // Total buildings
      prisma.building.count(),
      
      // Buildings with owner information
      prisma.building.count({
        where: {
          current_owner_name: { not: null },
        },
      }),
      
      // Recent sales (last 30 days)
      prisma.building.count({
        where: {
          sale_date: {
            gte: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
          },
        },
      }),
    ])

    const enrichmentRate = totalBuildings > 0 
      ? Math.round((buildingsWithOwners / totalBuildings) * 100)
      : 0

    const stats: MarketStats = {
      totalPermits,
      recentSales,
      totalProperties: totalBuildings,
      qualifiedLeads: permitsWithContacts,
      totalContacts: permitsWithContacts,
      buildingsWithOwners,
      enrichmentRate,
    }

    return NextResponse.json({
      success: true,
      data: stats,
    })
  } catch (error) {
    console.error('Error fetching market stats:', error)
    return NextResponse.json(
      {
        success: false,
        error: 'Failed to fetch market statistics',
      },
      { status: 500 }
    )
  }
}
