'use client'

import { useState, useEffect } from 'react'
import type { MarketStats } from '@/types'

export function useMarketStats() {
  const [stats, setStats] = useState<MarketStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchStats() {
      try {
        const response = await fetch('/api/stats')
        if (!response.ok) {
          throw new Error('Failed to fetch stats')
        }
        const data = await response.json()
        if (data.success) {
          setStats(data.data)
        } else {
          throw new Error(data.error || 'Unknown error')
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load stats')
        console.error('Error fetching market stats:', err)
      } finally {
        setIsLoading(false)
      }
    }

    fetchStats()
  }, [])

  return { stats, isLoading, error }
}
