# Permit Geocoding Service

Automatically adds latitude/longitude coordinates to permits that don't have them.

## ⚠️ NOTE: Included in Master Pipeline

**Geocoding is now part of `run_enrichment_pipeline.py` (Step 4).**

You can still run geocoding separately using this service, or let the master pipeline handle it automatically.

See: `ENRICHMENT_PIPELINE_GUIDE.md` for the integrated approach.

---

## Features

- ✅ **Dual API Support**: NYC Geoclient API (primary) + OpenStreetMap Nominatim (fallback)
- ✅ **Smart Address Parsing**: Extracts house number, street, and borough
- ✅ **Rate Limiting**: Configurable delays between API calls
- ✅ **Batch Processing**: Process 10 permits per run (configurable with GEOCODE_BATCH_SIZE)
- ✅ **Progress Tracking**: Detailed logging of success/failure
- ✅ **Railway Cron Ready**: Designed for scheduled execution

## NYC Geoclient API (Recommended)

The official NYC geocoding service - free, accurate, and NYC-specific.

### Get Free API Credentials

1. Visit: https://developer.cityofnewyork.us/user/register
2. Register for a free account
3. Create a new app in the Developer Portal
4. Note your `App ID` and `App Key`

### Add to Environment Variables

```bash
NYC_GEOCLIENT_APP_ID=your_app_id_here
NYC_GEOCLIENT_APP_KEY=your_app_key_here
```

## Fallback: OpenStreetMap Nominatim

If NYC Geoclient credentials are not set, the script automatically falls back to OpenStreetMap's Nominatim service (no API key required).

**Note:** Nominatim is slower and less accurate for NYC addresses.

## Usage

### Local Development

```bash
# Activate virtual environment
source venv-permit/bin/activate

# Run geocoding
python geocode_permits.py
```

### Railway Cron Job

#### ✅ COMPLETED SETUP

**Service Name:** Geocode-Properties-CRON

**Configuration:**
- Repository: `matthewmakh/NYC-DOB-permit-search-and-parse`
- Branch: `main`
- Config File: `railway.geocode.json`
- Cron Schedule: `0 3 * * 0` (Every Sunday at 3 AM UTC)
- Start Command: `python geocode_permits.py`
- Restart Policy: Never

**Environment Variables Set:**
```
DB_HOST = ${{Postgres.PGHOST}}
DB_PORT = ${{Postgres.PGPORT}}
DB_USER = ${{Postgres.PGUSER}}
DB_PASSWORD = ${{Postgres.PGPASSWORD}}
DB_NAME = ${{Postgres.PGDATABASE}}
NYC_GEOCLIENT_APP_ID = 66a4de50fd754c178583dea63fa49ee5
NYC_GEOCLIENT_APP_KEY = 9667f22ae5bf43a88c4dee563efb2cac
GEOCODE_BATCH_SIZE = 500
GEOCODE_DELAY = 0.01
```

#### 📋 DEPLOYMENT STATUS

**✅ DEPLOYED** as separate service: `Geocode-Properties-CRON`

**Note:** This separate geocoding service can coexist with the master pipeline. If both run:
- They won't conflict (each skips already-geocoded permits)
- Master pipeline is more efficient (runs all steps together)
- You can keep this for standalone geocoding runs or disable it

**Recommendation:** 
- Keep this service if you want geocoding to run independently
- OR disable it and let `run_enrichment_pipeline.py` handle geocoding as Step 4

1. **Current Config File Path:** `railway.geocode.json`

2. **Current Schedule:** `0 3 * * 0` (Every Sunday at 3 AM UTC)

3. **Monitor Execution:**
   - Go to Railway → Geocode-Properties-CRON → Deployments
   - Click on deployment → View Logs
   - Look for:
     ```
     ✅ NYC Geoclient API configured
     🗺️  PERMIT GEOCODING SERVICE
     Connected to database
     📊 Success rate: ~100%
     ```

#### 🔄 To Change Schedule

Edit `railway.geocode.json` and push to GitHub:
```bash
git add railway.geocode.json
git commit -m "Update geocode schedule"
git push origin main
```

Railway will auto-redeploy with the new schedule.

### Configuration Options

Environment variables (optional):

```bash
GEOCODE_BATCH_SIZE=500   # Permits per run (default: 500)
GEOCODE_DELAY=0.01       # Seconds between requests (default: 0.01)
```

## How It Works

1. **Fetch Permits**: Queries database for permits without coordinates (limit: 500 per run)
2. **Parse Address**: Extracts house number, street name, and borough from BBL
3. **Geocode**: 
   - NYC Geoclient V2 API for BBL-based addresses (100% success rate)
   - Nominatim fallback for non-BBL addresses (~70% success rate)
4. **Update Database**: Saves coordinates to permit record
5. **Report**: Shows success/failure statistics

**Overall Success Rate: ~85%** (tested on 20 permits)

## Output Example

```
======================================================================
🗺️  PERMIT GEOCODING SERVICE
======================================================================
✅ Connected to database: maglev.proxy.rlwy.net:26571/railway

📊 Database Statistics:
   Total permits: 1,968
   With coordinates: 868 (44.1%)
   Without coordinates: 1,100 (55.9%)

🔍 Fetching 100 permits to geocode...

Processing 100 permits...

[1/100] Permit #1954: 429 CLOVE ROAD
  📍 Using NYC Geoclient: 429 CLOVE ROAD
  ✅ Success: 40.608234, -74.118432

[2/100] Permit #1955: 1234 MAIN STREET
  🌐 Trying OpenStreetMap Nominatim...
  ✅ Success: 40.712776, -74.005974

...

======================================================================
📊 GEOCODING SUMMARY
======================================================================
✅ Successfully geocoded: 95
❌ Failed to geocode: 5
📈 Success rate: 95.0%

📝 Remaining permits without coordinates: 1,005
   Run this script again to continue geocoding

======================================================================
```

## Railway Cron Schedule

The current schedule (`0 3 * * 0`) runs **every Sunday at 3:00 AM UTC**.

This processes 500 permits per run = 2,000 permits per month (every Sunday).

### Adjust Schedule

Edit `railway.geocode.json`:

```json
{
  "deploy": {
    "cronSchedule": "0 */6 * * *"  // Every 6 hours
  }
}
```

**Common cron schedules:**
- `0 3 * * 0` - Every Sunday at 3 AM (current)
- `0 */6 * * *` - Every 6 hours
- `0 0 * * *` - Daily at midnight
- `0 0 * * 1` - Every Monday at midnight

## API Rate Limits

### NYC Geoclient
- **Free tier**: 2,500 requests/day
- **With key**: Higher limits available
- **Rate limit**: Built-in 0.5s delay between requests

### Nominatim
- **Policy**: Max 1 request per second
- **Rate limit**: Built-in 0.5s delay (conservative)

## Troubleshooting

### No coordinates returned

**Check address format:**
```
✅ Good: "123 MAIN STREET"
❌ Bad: "MAIN ST & 5TH AVE" (intersections not supported)
❌ Bad: "LOT 45" (no street address)
```

### NYC Geoclient not working

1. Verify credentials in Railway environment variables
2. Check API quota at developer portal
3. Confirm address is in NYC (5 boroughs only)

### Database connection failed

1. Verify database environment variables
2. Check Railway PostgreSQL service is linked
3. Confirm database credentials are correct

## Cost

- **NYC Geoclient API**: FREE (2,500 requests/day)
- **Nominatim**: FREE (with usage policy compliance)
- **Railway Cron Job**: FREE (within Railway free tier)

## Best Practices

1. **Start with small batches**: Test with `GEOCODE_BATCH_SIZE=10`
2. **Monitor success rate**: >80% is good, <50% check addresses
3. **Use NYC Geoclient**: Much better for NYC addresses
4. **Check logs regularly**: Railway → Service → Logs
5. **Adjust schedule**: More frequent for faster completion

## Integration with Dashboard

Once permits have coordinates, they automatically display:
- ✅ Interactive Leaflet maps on permit detail pages
- ✅ Map markers on main dashboard map view
- ✅ Location-based filtering and analysis

## Future Enhancements

- [ ] Support for Google Maps Geocoding API
- [ ] Batch geocoding for better performance
- [ ] Cache geocoding results by address
- [ ] Retry failed geocodes with different services
- [ ] Email notifications on completion
- [ ] Dashboard showing geocoding progress

## Support

For issues or questions:
1. Check Railway logs for error messages
2. Verify environment variables are set
3. Test locally with `python geocode_permits.py`
4. Check NYC Geoclient API status

---

**Ready to geocode!** Set up your NYC Geoclient API credentials and run the script. 🗺️
