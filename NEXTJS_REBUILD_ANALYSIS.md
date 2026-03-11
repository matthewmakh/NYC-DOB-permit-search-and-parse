# NYC Real Estate Intel Platform - Next.js Rebuild Analysis

**Date:** March 11, 2026  
**Branch Name:** `nextjs-rebuild-v2`  
**Tech Stack:** Next.js 14+ (App Router), TypeScript, Tailwind CSS, PostgreSQL

---

## 📋 Executive Summary

This document provides a comprehensive analysis of the existing Flask/HTML platform, identifies issues and areas for improvement, and outlines the architecture for the Next.js rebuild focusing initially on the Landing Page and Properties Page.

---

## 🎯 Platform Purpose

**NYC Real Estate Intelligence Platform** - A B2B SaaS tool for:
- Real estate investors finding off-market deals
- Contractors identifying construction opportunities  
- Property managers researching buildings
- Investors tracking portfolio properties

**Target Users:** Real estate professionals in NYC who need comprehensive property intelligence.

---

## 📊 Current Features Analysis

### 1. **Landing Page / Home** (`/`)
**Purpose:** Entry point with universal search and quick navigation to key sections.

**Current Features:**
- Universal search bar (address, BBL, owner name)
- Market snapshot stats (permits, sales, properties, leads)
- Quick access cards to different sections
- Hot activity feed showing recent market activity

**Issues Found:**
- ❌ Stats are hardcoded in some places (e.g., "1,361 Buildings", "94% Enriched")
- ❌ Search suggestions dropdown may have race conditions
- ❌ No loading states for stats
- ❌ Example searches link to non-functional handlers
- ❌ Activity feed items are static HTML, not dynamic

---

### 2. **Properties Page** (`/properties`)
**Purpose:** Browse and filter all tracked properties with advanced search capabilities.

**Current Features:**
- Advanced filtering sidebar:
  - Universal search
  - Owner name search
  - Assessed value range
  - Sale price range
  - Sale date range
  - Borough filter
  - Property type (residential/commercial/mixed)
  - Building class
  - Unit count range
  - Permit activity filters
  - Financing ratio
  - Cash purchase filter
  - HPD violations filter
  - Enrichable owner filter
- Stats dashboard
- Sortable property grid
- Pagination
- Bulk enrichment modal
- CSV export functionality

**Issues Found:**
- ❌ Property type filter logic is convoluted (mixing building class with land use)
- ❌ No debouncing on filter inputs causing excessive API calls
- ❌ Export can timeout on large datasets (no streaming)
- ❌ Bulk enrich modal doesn't validate payment method existence
- ❌ Property cards don't show loading skeletons
- ❌ No URL state persistence for filters (refreshing loses filters)
- ❌ Sort by "permits" uses permit_count but this isn't always populated

---

### 3. **Building Profile Page** (`/property/<bbl>`)
**Purpose:** Comprehensive "social media profile" for a building with all available data.

**Current Features:**
- Hero section with address and risk score
- Tab navigation: Overview, Financials, Owners, Transactions, Permits, Violations, Activity, Contacts
- Risk score calculation with breakdown
- Owner sources from multiple databases (PLUTO, RPAD, HPD, ACRIS, ECB)
- Property stats and quick metrics
- ACRIS transaction history
- Construction permits timeline
- HPD violations summary
- Contact enrichment (paid feature)

**Issues Found:**
- ❌ Risk score calculation is client-side, not consistent
- ❌ Tab content loads all at once instead of lazy loading
- ❌ No caching of building data
- ❌ Owner contact enrichment charges even on failed lookups (potential refund issue)
- ❌ Some tabs show "Loading..." indefinitely on error
- ❌ Modal close doesn't always work on mobile

---

### 4. **Construction Intelligence Page** (`/construction`)
**Purpose:** Map-based view of active construction permits with contractor tracking.

**Current Features:**
- Interactive Leaflet map with clustered markers
- Filter panel (date range, job type, status, units)
- Quick stats panel
- Bottom tabs: Recent Permits, Top Contractors
- CSV export

**Issues Found:**
- ❌ Map loading overlay can get stuck
- ❌ Marker clustering can be slow with 2000+ permits
- ❌ No way to save map view/filters
- ❌ Export doesn't include all visible data
- ❌ ContractorCard onclick handler missing proper encoding

---

### 5. **Contractors Page** (`/contractors`)
**Purpose:** Search and view contractor profiles.

**Current Features:**
- Contractor search
- Contractor profile with permit history
- License number lookup

**Issues Found:**
- ❌ Search is case-sensitive in some queries
- ❌ License lookup doesn't validate format
- ❌ No pagination on contractor permits lists

---

### 6. **Authentication System**
**Current Features:**
- Email/password signup and login
- Session management with cookies
- Stripe subscription integration ($250/month)
- Admin bypass for specific email
- Per-enrichment charges ($0.50 single, $0.35 batch)

**Issues Found:**
- ❌ Session token stored in plain cookie (should be httpOnly)
- ❌ Password reset flow not implemented
- ❌ No rate limiting on login attempts
- ❌ Stripe webhook verification uses wrong secret in some cases
- ❌ No email verification enforcement (user can login without verifying)
- ❌ Admin check is just email comparison (no proper role system)

---

### 7. **Enrichment Service**
**Purpose:** Contact lookup via Enformion API (primary) and Apify/TruePeopleSearch (fallback).

**Current Features:**
- Name parsing from various formats
- Phone/email lookup
- Result caching in database
- Batch enrichment with discounted pricing

**Issues Found:**
- ❌ Name parser rejects some valid names (e.g., lastname starting with "BANK")
- ❌ Apify fallback doesn't have proper timeout handling
- ❌ Enrichment results aren't deduplicated
- ❌ No retry logic on transient failures
- ❌ Charge happens before confirming API success

---

### 8. **Activity Logging**
**Purpose:** Track user actions for analytics and admin visibility.

**Current Features:**
- Page view tracking
- Search logging
- Export logging
- Error logging
- Admin activity dashboard

**Issues Found:**
- ❌ Logging is try/catch wrapped but errors are silently swallowed
- ❌ Activity logs table can grow unbounded (no cleanup)
- ❌ Some endpoints log twice due to middleware + manual logging

---

## 🗄️ Database Schema Summary

### Core Tables:
| Table | Records (Est.) | Purpose |
|-------|----------------|---------|
| `permits` | ~2,000 | DOB construction permits |
| `buildings` | ~1,400 | Deduplicated properties with enriched data |
| `acris_documents` | ~10,000 | ACRIS transaction records |
| `acris_parties` | ~20,000 | Buyers/sellers/lenders |
| `users` | ~50 | Platform users |
| `sessions` | ~200 | Active login sessions |
| `enrichment_results` | ~500 | Contact lookup cache |
| `activity_logs` | ~10,000 | User activity tracking |

### Key Relationships:
- `permits.bbl` → `buildings.bbl`
- `acris_documents.bbl` → `buildings.bbl`
- `permits.building_id` → `buildings.id`

---

## 🔧 Technical Debt Identified

### Backend (Flask)
1. **Connection Pool Issues:** Pool recreation on every error can exhaust connections
2. **No Request Validation:** SQL injection possible on some endpoints
3. **Duplicate Routes:** Two `/api/permit/<permit_id>` routes exist
4. **Cache Invalidation:** Stats cached but never invalidated on data changes
5. **Large Query Responses:** Some endpoints return entire dataset without pagination
6. **No API Versioning:** Breaking changes affect all clients

### Frontend (Vanilla JS)
1. **Global State:** Uses window variables for state management
2. **No Error Boundaries:** JS errors break entire page
3. **Accessibility Issues:** Missing ARIA labels, keyboard navigation
4. **No Loading States:** Many operations have no user feedback
5. **Memory Leaks:** Event listeners not cleaned up on page navigation
6. **No Offline Support:** No service worker or caching strategy

### Security
1. **Exposed API Keys:** Some keys visible in client-side code
2. **CORS Too Permissive:** `CORS(app)` allows all origins
3. **No CSRF Protection:** Form submissions vulnerable
4. **Session Fixation:** Sessions not regenerated on privilege change

---

## 🚀 Next.js Rebuild Architecture

### Tech Stack
- **Framework:** Next.js 14+ (App Router)
- **Language:** TypeScript (strict mode)
- **Styling:** Tailwind CSS + shadcn/ui components
- **State:** React Query (TanStack Query) for server state
- **Forms:** React Hook Form + Zod validation
- **Auth:** NextAuth.js with Postgres adapter
- **Database:** PostgreSQL (existing) via Prisma ORM
- **Payments:** Stripe (existing integration)
- **Maps:** React-Leaflet or Mapbox GL

### Project Structure
```
nyc-real-estate-intel/
├── app/
│   ├── (auth)/
│   │   ├── login/
│   │   └── signup/
│   ├── (dashboard)/
│   │   ├── layout.tsx          # Authenticated layout with nav
│   │   ├── page.tsx            # Landing/Home
│   │   ├── properties/
│   │   │   ├── page.tsx        # Properties list
│   │   │   └── [bbl]/
│   │   │       └── page.tsx    # Building profile
│   │   ├── construction/
│   │   ├── contractors/
│   │   └── admin/
│   ├── api/
│   │   ├── auth/
│   │   ├── properties/
│   │   ├── permits/
│   │   ├── enrichment/
│   │   └── webhooks/
│   ├── layout.tsx
│   └── globals.css
├── components/
│   ├── ui/                     # shadcn components
│   ├── properties/
│   ├── maps/
│   └── shared/
├── lib/
│   ├── db.ts                   # Prisma client
│   ├── auth.ts                 # NextAuth config
│   ├── stripe.ts
│   └── utils.ts
├── hooks/
│   ├── use-properties.ts
│   ├── use-building.ts
│   └── use-filters.ts
├── types/
│   └── index.ts
└── prisma/
    └── schema.prisma
```

### Phase 1 Scope (This PR)
1. ✅ Landing Page with search
2. ✅ Properties Page with filters
3. ✅ Basic authentication
4. ✅ API routes for properties

### Phase 2 (Future)
- Building Profile page
- Construction Intelligence page
- Enrichment features
- Admin dashboard

---

## 📝 Issues to Fix in Rebuild

### Critical (Blocking)
1. [ ] Implement proper authentication with NextAuth
2. [ ] Add request validation on all API routes
3. [ ] Implement proper error handling with user feedback
4. [ ] Add loading states and skeletons throughout

### High Priority
5. [ ] URL state persistence for filters
6. [ ] Debounced search inputs
7. [ ] Paginated API responses
8. [ ] Proper TypeScript types for all data

### Medium Priority
9. [ ] Lazy loading for tabs/sections
10. [ ] Export with streaming for large datasets
11. [ ] Better map performance with virtualization
12. [ ] Accessibility compliance (WCAG 2.1)

### Low Priority (Nice to Have)
13. [ ] Offline support with service worker
14. [ ] Push notifications for saved searches
15. [ ] Dark/light theme toggle
16. [ ] Mobile app with React Native

---

## 🎨 Design System

### Colors
```css
/* Primary */
--primary: #8b5cf6;        /* Purple */
--primary-dark: #7c3aed;
--primary-light: #a78bfa;

/* Secondary */
--secondary: #ec4899;       /* Pink */

/* Accent */
--accent: #10b981;          /* Green */

/* Backgrounds (Dark Theme) */
--bg-primary: #0a0e1a;
--bg-secondary: #111827;
--bg-tertiary: #1f2937;

/* Text */
--text-primary: #f9fafb;
--text-secondary: #d1d5db;
--text-muted: #9ca3af;
```

### Typography
- Font: System font stack (-apple-system, BlinkMacSystemFont, etc.)
- Headings: 700 weight
- Body: 400 weight
- Small: 14px, Regular: 16px, Large: 18px

### Components to Build
- [ ] Button (primary, secondary, outline, ghost)
- [ ] Card (property card, stat card, info card)
- [ ] Input (text, search, select, date)
- [ ] Modal/Dialog
- [ ] Tabs
- [ ] Pagination
- [ ] Table (sortable, filterable)
- [ ] Badge/Pill
- [ ] Skeleton loader
- [ ] Toast notifications

---

## ✅ Success Criteria

### Landing Page
- [ ] Search works for address, BBL, owner name
- [ ] Stats load dynamically from API
- [ ] Quick access cards navigate correctly
- [ ] Mobile responsive
- [ ] Page loads under 2 seconds

### Properties Page
- [ ] All filters work correctly
- [ ] Filters persist in URL
- [ ] Pagination works
- [ ] Sort works on all columns
- [ ] Export generates valid CSV
- [ ] Property cards link to profile
- [ ] Mobile responsive
- [ ] Handles 1000+ properties smoothly

---

*This document will be updated as the rebuild progresses.*
