#!/usr/bin/env python3
"""
Calculate Property Intelligence
Analyzes ACRIS transaction data to score buildings for lead generation campaigns
Populates the property_intelligence table with calculated metrics
"""

import psycopg2
import psycopg2.extras
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Database connection
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def calculate_flip_metrics(building_id, cur):
    """
    Calculate flip-related metrics
    Returns: is_likely_flipper, flip_score, sale_velocity_months
    """
    # Get all deeds for this building
    cur.execute("""
        SELECT doc_date, doc_amount
        FROM acris_transactions
        WHERE building_id = %s
        AND doc_type LIKE '%%DEED%%'
        AND doc_date IS NOT NULL
        ORDER BY doc_date DESC
    """, (building_id,))
    
    deeds = cur.fetchall()
    
    if len(deeds) < 2:
        # Not enough sales to determine flip pattern
        return False, 0, None
    
    # Calculate time between sales
    sale_dates = [deed['doc_date'] for deed in deeds]
    time_diffs = []
    
    for i in range(len(sale_dates) - 1):
        diff_days = (sale_dates[i] - sale_dates[i+1]).days
        time_diffs.append(diff_days)
    
    avg_days_between_sales = sum(time_diffs) / len(time_diffs) if time_diffs else None
    sale_velocity_months = round(avg_days_between_sales / 30, 2) if avg_days_between_sales else None
    
    # Flip score calculation (0-100)
    flip_score = 0
    
    # More sales = higher score
    if len(deeds) >= 5:
        flip_score += 40
    elif len(deeds) >= 3:
        flip_score += 25
    elif len(deeds) == 2:
        flip_score += 10
    
    # Fast turnover = higher score
    if sale_velocity_months:
        if sale_velocity_months <= 12:  # Less than 1 year
            flip_score += 40
        elif sale_velocity_months <= 24:  # Less than 2 years
            flip_score += 25
        elif sale_velocity_months <= 36:  # Less than 3 years
            flip_score += 15
    
    # Recent activity = higher score
    most_recent_sale = sale_dates[0]
    days_since_last_sale = (datetime.now().date() - most_recent_sale).days
    if days_since_last_sale <= 365:  # Within last year
        flip_score += 20
    elif days_since_last_sale <= 730:  # Within 2 years
        flip_score += 10
    
    # Likely flipper if >= 3 sales in last 5 years
    five_years_ago = datetime.now().date() - timedelta(days=5*365)
    recent_sales = [d for d in sale_dates if d >= five_years_ago]
    is_likely_flipper = len(recent_sales) >= 3
    
    return is_likely_flipper, min(flip_score, 100), sale_velocity_months


def calculate_investment_profile(building_id, cur):
    """
    Calculate investment profile metrics
    Returns: is_cash_investor, is_heavy_leverage, equity_percentage
    """
    # Get primary sale and mortgage
    cur.execute("""
        SELECT 
            b.sale_price,
            b.is_cash_purchase,
            (SELECT SUM(doc_amount) 
             FROM acris_transactions 
             WHERE building_id = %s 
             AND doc_type = 'MTGE' 
             AND doc_date >= b.sale_date - INTERVAL '90 days'
             AND doc_date <= b.sale_date + INTERVAL '90 days'
            ) as mortgage_amount
        FROM buildings b
        WHERE b.id = %s
    """, (building_id, building_id))
    
    result = cur.fetchone()
    
    if not result or not result['sale_price']:
        return False, False, None
    
    sale_price = float(result['sale_price'])
    is_cash_purchase = result['is_cash_purchase']
    mortgage_amount = float(result['mortgage_amount'] or 0)
    
    # Cash investor detection
    is_cash_investor = is_cash_purchase
    
    # Heavy leverage detection (LTV > 80%)
    ltv_ratio = (mortgage_amount / sale_price * 100) if sale_price > 0 else 0
    is_heavy_leverage = ltv_ratio > 80
    
    # Equity percentage
    equity_percentage = round(100 - ltv_ratio, 2) if sale_price > 0 else None
    
    return is_cash_investor, is_heavy_leverage, equity_percentage


def calculate_price_trends(building_id, cur):
    """
    Calculate appreciation metrics
    Returns: appreciation_amount, appreciation_percent, price_per_sqft_at_sale
    """
    # Get most recent two sales
    cur.execute("""
        SELECT doc_date, doc_amount
        FROM acris_transactions
        WHERE building_id = %s
        AND doc_type LIKE '%%DEED%%'
        AND doc_amount > 0
        AND doc_date IS NOT NULL
        ORDER BY doc_date DESC
        LIMIT 2
    """, (building_id,))
    
    sales = cur.fetchall()
    
    if len(sales) < 2:
        return None, None, None
    
    current_sale = float(sales[0]['doc_amount'])
    previous_sale = float(sales[1]['doc_amount'])
    
    appreciation_amount = round(current_sale - previous_sale, 2)
    appreciation_percent = round((appreciation_amount / previous_sale * 100), 2) if previous_sale > 0 else None
    
    # Get building size for price per sqft
    cur.execute("""
        SELECT building_sqft
        FROM buildings
        WHERE id = %s
    """, (building_id,))
    
    building = cur.fetchone()
    building_sqft = building['building_sqft'] if building else None
    
    price_per_sqft_at_sale = round(current_sale / building_sqft, 2) if building_sqft and building_sqft > 0 else None
    
    return appreciation_amount, appreciation_percent, price_per_sqft_at_sale


def calculate_contact_value(building_id, cur):
    """
    Calculate contact availability metrics
    Returns: has_seller_address, has_lender_info, multi_property_owner
    """
    # Check if any sellers have addresses
    cur.execute("""
        SELECT COUNT(*) as seller_count
        FROM acris_parties
        WHERE building_id = %s
        AND party_type = 'seller'
        AND is_lead = TRUE
        AND address_1 IS NOT NULL
        AND address_1 != ''
    """, (building_id,))
    
    result = cur.fetchone()
    has_seller_address = result['seller_count'] > 0 if result else False
    
    # Check if lender info exists
    cur.execute("""
        SELECT COUNT(*) as lender_count
        FROM acris_parties
        WHERE building_id = %s
        AND party_type = 'lender'
    """, (building_id,))
    
    result = cur.fetchone()
    has_lender_info = result['lender_count'] > 0 if result else False
    
    # Check if owner has multiple properties
    cur.execute("""
        SELECT 
            b.current_owner_name,
            (SELECT COUNT(*) 
             FROM buildings b2 
             WHERE b2.current_owner_name = b.current_owner_name
             AND b2.current_owner_name IS NOT NULL
             AND b2.current_owner_name != ''
            ) as property_count
        FROM buildings b
        WHERE b.id = %s
    """, (building_id,))
    
    result = cur.fetchone()
    multi_property_owner = result['property_count'] > 1 if result and result['property_count'] else False
    
    return has_seller_address, has_lender_info, multi_property_owner


def calculate_lead_score(flip_score, is_likely_flipper, is_cash_investor, is_heavy_leverage, 
                         has_seller_address, has_lender_info, days_since_last_sale):
    """
    Calculate overall lead score (0-100) based on multiple factors
    """
    score = 0
    
    # Flipper activity (0-40 points)
    if is_likely_flipper:
        score += 20
    score += min(flip_score * 0.2, 20)  # Scale flip_score to 0-20
    
    # Cash purchase pattern (0-30 points)
    if is_cash_investor:
        score += 30
    elif is_heavy_leverage:
        score += 10  # Heavy leverage can also be interesting (potential refinance opportunity)
    
    # Recent transaction (0-20 points)
    if days_since_last_sale:
        if days_since_last_sale <= 180:  # Within 6 months
            score += 20
        elif days_since_last_sale <= 365:  # Within 1 year
            score += 15
        elif days_since_last_sale <= 730:  # Within 2 years
            score += 10
        elif days_since_last_sale <= 1095:  # Within 3 years
            score += 5
    
    # Seller address available (0-15 points) - KEY for "Previous Owners Campaign"
    if has_seller_address:
        score += 15
    
    # Lender info available (0-5 points)
    if has_lender_info:
        score += 5
    
    return min(int(score), 100)


def determine_lead_priority(lead_score):
    """
    Categorize lead priority based on score
    """
    if lead_score >= 70:
        return 'high'
    elif lead_score >= 40:
        return 'medium'
    else:
        return 'low'


def calculate_property_intelligence():
    """
    Main function: Calculate intelligence metrics for all buildings with ACRIS data
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("Property Intelligence Calculator")
    print("=" * 70)
    
    # Get all buildings that have ACRIS data
    cur.execute("""
        SELECT id, bbl, address, current_owner_name, sale_date
        FROM buildings
        WHERE acris_last_enriched IS NOT NULL
        AND acris_total_transactions > 0
        ORDER BY id
    """)
    
    buildings = cur.fetchall()
    print(f"\n📊 Found {len(buildings)} buildings with ACRIS data to analyze")
    
    if not buildings:
        print("   ℹ️  No buildings to analyze yet. Run step3_enrich_from_acris.py first.")
        cur.close()
        conn.close()
        return
    
    processed = 0
    high_priority = 0
    medium_priority = 0
    low_priority = 0
    
    for i, building in enumerate(buildings, 1):
        building_id = building['id']
        
        # Debug mode - show first 5 buildings in detail
        debug = i <= 5
        
        if debug:
            print(f"\n{'='*70}")
            print(f"🔍 [{i}] BBL {building['bbl']}: {building['address']}")
            print(f"    Owner: {building['current_owner_name']}")
        
        try:
            # Calculate all metrics
            is_likely_flipper, flip_score, sale_velocity_months = calculate_flip_metrics(building_id, cur)
            is_cash_investor, is_heavy_leverage, equity_percentage = calculate_investment_profile(building_id, cur)
            appreciation_amount, appreciation_percent, price_per_sqft_at_sale = calculate_price_trends(building_id, cur)
            has_seller_address, has_lender_info, multi_property_owner = calculate_contact_value(building_id, cur)
            
            # Calculate days since last sale
            days_since_last_sale = None
            if building['sale_date']:
                days_since_last_sale = (datetime.now().date() - building['sale_date']).days
            
            if debug:
                print(f"\n    📊 Metrics Calculated:")
                print(f"       Flip Score: {flip_score}/100")
                print(f"       Is Likely Flipper: {is_likely_flipper}")
                print(f"       Sale Velocity: {sale_velocity_months} months" if sale_velocity_months else "       Sale Velocity: N/A (< 2 sales)")
                print(f"       Is Cash Investor: {is_cash_investor}")
                print(f"       Is Heavy Leverage: {is_heavy_leverage}")
                print(f"       Equity %: {equity_percentage}%" if equity_percentage else "       Equity %: N/A")
                print(f"       Appreciation: ${appreciation_amount:,.0f} ({appreciation_percent:.1f}%)" if appreciation_amount else "       Appreciation: N/A")
                print(f"       Has Seller Address: {has_seller_address}")
                print(f"       Has Lender Info: {has_lender_info}")
                print(f"       Multi-Property Owner: {multi_property_owner}")
                print(f"       Days Since Last Sale: {days_since_last_sale}" if days_since_last_sale else "       Days Since Last Sale: N/A")
            
            # Calculate lead score with breakdown
            score_breakdown = []
            score = 0
            
            # Flipper activity (0-40 points)
            flipper_points = 0
            if is_likely_flipper:
                flipper_points += 20
                score_breakdown.append("Likely Flipper: +20")
            flipper_bonus = min(flip_score * 0.2, 20)
            flipper_points += flipper_bonus
            if flipper_bonus > 0:
                score_breakdown.append(f"Flip Score Bonus: +{flipper_bonus:.0f}")
            score += flipper_points
            
            # Cash purchase pattern (0-30 points)
            cash_points = 0
            if is_cash_investor:
                cash_points = 30
                score_breakdown.append("Cash Purchase: +30")
            elif is_heavy_leverage:
                cash_points = 10
                score_breakdown.append("Heavy Leverage: +10")
            score += cash_points
            
            # Recent transaction (0-20 points)
            recency_points = 0
            if days_since_last_sale:
                if days_since_last_sale <= 180:
                    recency_points = 20
                    score_breakdown.append("Sale <6mo: +20")
                elif days_since_last_sale <= 365:
                    recency_points = 15
                    score_breakdown.append("Sale <1yr: +15")
                elif days_since_last_sale <= 730:
                    recency_points = 10
                    score_breakdown.append("Sale <2yr: +10")
                elif days_since_last_sale <= 1095:
                    recency_points = 5
                    score_breakdown.append("Sale <3yr: +5")
            score += recency_points
            
            # Seller address available (0-15 points)
            address_points = 0
            if has_seller_address:
                address_points = 15
                score_breakdown.append("Seller Address: +15")
            score += address_points
            
            # Lender info (0-5 points)
            lender_points = 0
            if has_lender_info:
                lender_points = 5
                score_breakdown.append("Lender Info: +5")
            score += lender_points
            
            lead_score = min(int(score), 100)
            lead_priority = determine_lead_priority(lead_score)
            
            if debug:
                print(f"\n    🎯 Lead Score Breakdown:")
                if score_breakdown:
                    for item in score_breakdown:
                        print(f"       {item}")
                else:
                    print(f"       No scoring factors found")
                print(f"\n    ✅ TOTAL SCORE: {lead_score}/100 ({lead_priority.upper()})")
            
            # Save to database
            cur.execute("""
                INSERT INTO property_intelligence (
                    building_id, is_likely_flipper, flip_score, sale_velocity_months,
                    is_cash_investor, is_heavy_leverage, equity_percentage,
                    appreciation_amount, appreciation_percent, price_per_sqft_at_sale,
                    has_seller_address, has_lender_info, multi_property_owner,
                    lead_score, lead_priority, calculated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (building_id) 
                DO UPDATE SET
                    is_likely_flipper = EXCLUDED.is_likely_flipper,
                    flip_score = EXCLUDED.flip_score,
                    sale_velocity_months = EXCLUDED.sale_velocity_months,
                    is_cash_investor = EXCLUDED.is_cash_investor,
                    is_heavy_leverage = EXCLUDED.is_heavy_leverage,
                    equity_percentage = EXCLUDED.equity_percentage,
                    appreciation_amount = EXCLUDED.appreciation_amount,
                    appreciation_percent = EXCLUDED.appreciation_percent,
                    price_per_sqft_at_sale = EXCLUDED.price_per_sqft_at_sale,
                    has_seller_address = EXCLUDED.has_seller_address,
                    has_lender_info = EXCLUDED.has_lender_info,
                    multi_property_owner = EXCLUDED.multi_property_owner,
                    lead_score = EXCLUDED.lead_score,
                    lead_priority = EXCLUDED.lead_priority,
                    calculated_at = NOW()
            """, (
                building_id, is_likely_flipper, flip_score, sale_velocity_months,
                is_cash_investor, is_heavy_leverage, equity_percentage,
                appreciation_amount, appreciation_percent, price_per_sqft_at_sale,
                has_seller_address, has_lender_info, multi_property_owner,
                lead_score, lead_priority
            ))
            
            conn.commit()
            processed += 1
            
            # Count by priority
            if lead_priority == 'high':
                high_priority += 1
            elif lead_priority == 'medium':
                medium_priority += 1
            else:
                low_priority += 1
            
            # Show progress every 50 buildings
            if i % 50 == 0:
                print(f"   ...processed {i}/{len(buildings)} buildings")
        
        except Exception as e:
            print(f"   ⚠️  Error processing building {building_id}: {e}")
            continue
    
    print(f"\n" + "=" * 70)
    print(f"✅ Property Intelligence Complete!")
    print(f"   Buildings analyzed: {processed}")
    print(f"   High priority leads: {high_priority}")
    print(f"   Medium priority leads: {medium_priority}")
    print(f"   Low priority leads: {low_priority}")
    
    # Show top scoring buildings
    cur.execute("""
        SELECT 
            b.bbl, b.address, b.current_owner_name,
            pi.lead_score, pi.lead_priority,
            pi.is_likely_flipper, pi.is_cash_investor,
            pi.has_seller_address, pi.appreciation_percent
        FROM buildings b
        JOIN property_intelligence pi ON b.id = pi.building_id
        ORDER BY pi.lead_score DESC
        LIMIT 10
    """)
    
    top_leads = cur.fetchall()
    
    if top_leads:
        print(f"\n🎯 Top 10 Lead Opportunities:")
        for lead in top_leads:
            print(f"\n   BBL {lead['bbl']}: {lead['address']}")
            print(f"   Owner: {lead['current_owner_name']}")
            print(f"   Lead Score: {lead['lead_score']}/100 ({lead['lead_priority'].upper()})")
            flags = []
            if lead['is_likely_flipper']:
                flags.append("🔄 FLIPPER")
            if lead['is_cash_investor']:
                flags.append("💵 CASH")
            if lead['has_seller_address']:
                flags.append("📬 HAS ADDRESS")
            if lead['appreciation_percent'] and lead['appreciation_percent'] > 20:
                flags.append(f"📈 +{lead['appreciation_percent']:.0f}%")
            if flags:
                print(f"   Flags: {' | '.join(flags)}")
    
    # Show seller leads with addresses
    cur.execute("""
        SELECT COUNT(DISTINCT b.id) as building_count,
               COUNT(ap.id) as seller_count
        FROM buildings b
        JOIN property_intelligence pi ON b.id = pi.building_id
        JOIN acris_parties ap ON b.id = ap.building_id
        WHERE ap.party_type = 'seller'
        AND ap.is_lead = TRUE
        AND ap.address_1 IS NOT NULL
        AND ap.address_1 != ''
    """)
    
    seller_stats = cur.fetchone()
    if seller_stats:
        print(f"\n💰 Previous Owners Campaign Ready:")
        print(f"   {seller_stats['building_count']} buildings with seller addresses")
        print(f"   {seller_stats['seller_count']} total seller contacts available")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    calculate_property_intelligence()
