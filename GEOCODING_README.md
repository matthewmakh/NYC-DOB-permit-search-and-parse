# Permit Geocoding Service

Automatically adds latitude/longitude coordinates to permits that don't have them.

## Features

- ✅ **Dual API Support**: NYC Geoclient API (primary) + OpenStreetMap Nominatim (fallback)
- ✅ **Smart Address Parsing**: Extracts house number, street, and borough
- ✅ **Rate Limiting**: Configurable delays between API calls
- ✅ **Batch Processing**: Process 100 permits per run (configurable)
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

1. **Create Cron Service in Railway:**
   ```
   New Service → GitHub Repo → Select branch
   ```

2. **Set Environment Variables:**
   ```
   DB_HOST = ${{Postgres.PGHOST}}
   DB_PORT = ${{Postgres.PGPORT}}
   DB_USER = ${{Postgres.PGUSER}}
   DB_PASSWORD = ${{Postgres.PGPASSWORD}}
   DB_NAME = ${{Postgres.PGDATABASE}}
   NYC_GEOCLIENT_APP_ID = your_app_id
   NYC_GEOCLIENT_APP_KEY = your_app_key
   ```

3. **Configure Cron Schedule:**
   - Use `railway.geocode.json` configuration
   - Or manually set cron: `0 */6 * * *` (every 6 hours)

4. **Set Start Command:**
   ```bash
   python geocode_permits.py
   ```

### Configuration Options

Environment variables (optional):

```bash
GEOCODE_BATCH_SIZE=100   # Permits per run (default: 100)
GEOCODE_DELAY=0.5        # Seconds between requests (default: 0.5)
```

## How It Works

1. **Fetch Permits**: Queries database for permits without coordinates
2. **Parse Address**: Extracts house number, street name, and borough
3. **Geocode**: 
   - Try NYC Geoclient API (if configured)
   - Fallback to Nominatim if needed
4. **Update Database**: Saves coordinates to permit record
5. **Report**: Shows success/failure statistics

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

The default schedule (`0 */6 * * *`) runs every 6 hours:
- 00:00 (midnight)
- 06:00 (6 AM)
- 12:00 (noon)
- 18:00 (6 PM)

This processes 100 permits per run = 400 permits per day.

### Adjust Schedule

Edit `railway.geocode.json`:

```json
{
  "deploy": {
    "cronSchedule": "0 */3 * * *"  // Every 3 hours
  }
}
```

Or use Railway dashboard to set custom schedule.

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
