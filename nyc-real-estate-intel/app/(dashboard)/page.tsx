'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Search, Building2, HardHat, TrendingUp, BarChart3, Users, Filter } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useMarketStats } from '@/hooks/use-market-stats'
import { isBBLFormat, cleanBBL } from '@/lib/utils'

export default function HomePage() {
  const router = useRouter()
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearching, setIsSearching] = useState(false)
  const { stats, isLoading: statsLoading } = useMarketStats()

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!searchQuery.trim()) return

    setIsSearching(true)
    
    // Check if it's a BBL format and redirect directly
    if (isBBLFormat(searchQuery)) {
      const cleanedBBL = cleanBBL(searchQuery)
      router.push(`/properties/${cleanedBBL}`)
      return
    }
    
    // Otherwise, go to search results
    router.push(`/properties?search=${encodeURIComponent(searchQuery.trim())}`)
  }

  const quickAccessCards = [
    {
      title: 'Active Construction Projects',
      description: 'Find permits, project pipelines, and contractor opportunities',
      icon: HardHat,
      emoji: '🏗️',
      href: '/construction',
      stats: [
        { label: 'Permits', value: stats?.totalPermits || 0 },
        { label: 'With Contacts', value: stats?.totalContacts || 0 },
      ],
      color: 'from-purple-500 to-purple-700',
    },
    {
      title: 'Investment Opportunities',
      description: 'Recent sales, market trends, and seller leads',
      icon: TrendingUp,
      emoji: '💰',
      href: '/investments',
      stats: [
        { label: 'Recent Sales', value: stats?.recentSales || 0 },
        { label: 'Properties', value: stats?.totalProperties || 0 },
      ],
      color: 'from-green-500 to-green-700',
    },
    {
      title: 'Property Database',
      description: 'Comprehensive property data with owner intelligence',
      icon: Building2,
      emoji: '🏢',
      href: '/properties',
      stats: [
        { label: 'Buildings', value: stats?.totalProperties || 0 },
        { label: 'Enriched', value: `${stats?.enrichmentRate || 0}%` },
      ],
      color: 'from-blue-500 to-blue-700',
    },
    {
      title: 'Market Analytics',
      description: 'Trends, hot zones, and neighborhood insights',
      icon: BarChart3,
      emoji: '📊',
      href: '/analytics',
      stats: [
        { label: 'Boroughs', value: '5' },
        { label: 'Real-time', value: '✓' },
      ],
      color: 'from-orange-500 to-orange-700',
    },
    {
      title: 'Owner Intelligence',
      description: 'Track owners, portfolios, and investment patterns',
      icon: Users,
      emoji: '👥',
      href: '/owners',
      stats: [
        { label: 'Owners', value: stats?.buildingsWithOwners || 0 },
        { label: 'Portfolios', value: 'Multi' },
      ],
      color: 'from-pink-500 to-pink-700',
    },
    {
      title: 'Advanced Search',
      description: 'Bulk queries, custom filters, and data exports',
      icon: Filter,
      emoji: '🔍',
      href: '/properties',
      stats: [
        { label: 'Power Tools', value: '✓' },
        { label: 'CSV Export', value: '✓' },
      ],
      color: 'from-cyan-500 to-cyan-700',
    },
  ]

  return (
    <div className="flex flex-col">
      {/* Hero Section */}
      <section className="relative py-20 px-4 bg-gradient-to-b from-background via-background to-card">
        <div className="container max-w-4xl mx-auto text-center">
          <h1 className="text-4xl md:text-5xl font-bold mb-4 text-gradient">
            Everything you need to know about any NYC property
          </h1>
          <p className="text-lg text-muted-foreground mb-8">
            Search by address, BBL, or owner name to get comprehensive property intelligence
          </p>

          {/* Search Bar */}
          <form onSubmit={handleSearch} className="relative max-w-2xl mx-auto">
            <div className="relative">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground" />
              <Input
                type="text"
                placeholder="Enter address, BBL (e.g., 1-00234-0056), or owner name..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-14 pl-12 pr-32 text-lg rounded-full border-2 focus:border-primary"
              />
              <Button
                type="submit"
                size="lg"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full"
                disabled={isSearching}
              >
                {isSearching ? 'Searching...' : 'Search'}
              </Button>
            </div>
          </form>

          {/* Quick Examples */}
          <div className="mt-4 flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <span>Try:</span>
            <button
              onClick={() => setSearchQuery('123 Main Street, Manhattan')}
              className="text-primary hover:underline"
            >
              123 Main Street
            </button>
            <span>•</span>
            <button
              onClick={() => setSearchQuery('1-00234-0056')}
              className="text-primary hover:underline"
            >
              BBL: 1-00234-0056
            </button>
            <span>•</span>
            <button
              onClick={() => setSearchQuery('ABC Realty LLC')}
              className="text-primary hover:underline"
            >
              ABC Realty LLC
            </button>
          </div>
        </div>
      </section>

      {/* Market Snapshot */}
      <section className="py-12 px-4 border-y bg-card/50">
        <div className="container">
          <h2 className="text-xl font-semibold mb-6 flex items-center gap-2">
            📊 Market Snapshot
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              icon="🏗️"
              value={stats?.totalPermits}
              label="Total Permits"
              sublabel="Since 2000"
              loading={statsLoading}
            />
            <StatCard
              icon="💰"
              value={stats?.recentSales}
              label="Sales (Last 30 Days)"
              sublabel="Recent activity"
              loading={statsLoading}
            />
            <StatCard
              icon="🏢"
              value={stats?.totalProperties}
              label="Properties Tracked"
              sublabel={`${stats?.enrichmentRate}% enriched`}
              loading={statsLoading}
            />
            <StatCard
              icon="👥"
              value={stats?.qualifiedLeads}
              label="Qualified Leads"
              sublabel={`${stats?.totalContacts} contacts`}
              loading={statsLoading}
            />
          </div>
        </div>
      </section>

      {/* Quick Access Cards */}
      <section className="py-12 px-4">
        <div className="container">
          <h2 className="text-xl font-semibold mb-6 flex items-center gap-2">
            🎯 What are you looking for?
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {quickAccessCards.map((card) => (
              <QuickAccessCard key={card.title} {...card} loading={statsLoading} />
            ))}
          </div>
        </div>
      </section>

      {/* Hot Activity Feed */}
      <section className="py-12 px-4 bg-card/30">
        <div className="container">
          <h2 className="text-xl font-semibold mb-6 flex items-center gap-2">
            🔥 Recent Activity
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            <ActivityItem
              icon="🏗️"
              title="Williamsburg"
              description="23 new permits filed"
              time="2 hours ago"
            />
            <ActivityItem
              icon="💰"
              title="Park Slope"
              description="$4.2M property sold"
              time="5 hours ago"
            />
            <ActivityItem
              icon="📋"
              title="Long Island City"
              description="Major development approved"
              time="1 day ago"
            />
          </div>
        </div>
      </section>
    </div>
  )
}

// Sub-components
function StatCard({
  icon,
  value,
  label,
  sublabel,
  loading,
}: {
  icon: string
  value: number | string | undefined
  label: string
  sublabel: string
  loading: boolean
}) {
  return (
    <Card className="bg-card/50 border-border/50">
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <span className="text-2xl">{icon}</span>
          <div>
            {loading ? (
              <Skeleton className="h-8 w-20 mb-1" />
            ) : (
              <div className="text-2xl font-bold">
                {typeof value === 'number' ? value.toLocaleString() : value || '0'}
              </div>
            )}
            <div className="text-sm text-muted-foreground">{label}</div>
            <div className="text-xs text-muted-foreground/70">{sublabel}</div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function QuickAccessCard({
  title,
  description,
  emoji,
  href,
  stats,
  color,
  loading,
}: {
  title: string
  description: string
  icon: React.ElementType
  emoji: string
  href: string
  stats: { label: string; value: string | number }[]
  color: string
  loading: boolean
}) {
  const router = useRouter()
  
  return (
    <Card
      className="group cursor-pointer transition-all hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5"
      onClick={() => router.push(href)}
    >
      <CardContent className="p-6">
        <div className="flex items-start justify-between mb-4">
          <span className="text-3xl">{emoji}</span>
          <span className="text-muted-foreground group-hover:text-primary transition-colors">
            →
          </span>
        </div>
        <h3 className="text-lg font-semibold mb-2 group-hover:text-primary transition-colors">
          {title}
        </h3>
        <p className="text-sm text-muted-foreground mb-4">{description}</p>
        <div className="flex gap-3">
          {stats.map((stat) => (
            <div
              key={stat.label}
              className="px-2 py-1 rounded-md bg-muted text-xs"
            >
              {loading ? (
                <Skeleton className="h-4 w-16" />
              ) : (
                <>
                  <span className="font-medium">
                    {typeof stat.value === 'number'
                      ? stat.value.toLocaleString()
                      : stat.value}
                  </span>{' '}
                  <span className="text-muted-foreground">{stat.label}</span>
                </>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function ActivityItem({
  icon,
  title,
  description,
  time,
}: {
  icon: string
  title: string
  description: string
  time: string
}) {
  return (
    <div className="flex items-start gap-3 p-4 rounded-lg bg-card border">
      <span className="text-xl">{icon}</span>
      <div className="flex-1">
        <div className="font-medium">{title}</div>
        <div className="text-sm text-muted-foreground">{description}</div>
        <div className="text-xs text-muted-foreground/70 mt-1">{time}</div>
      </div>
    </div>
  )
}
