# Database Restructure Complete - Contacts Table for Phone Validation

## ✅ What Was Created

### 1. Migration Script: `migrate_restructure_contacts.py`
- Drops old contacts table
- Creates new contacts table with phone validation fields
- Creates permit_contacts junction table (many-to-many)
- Extracts unique contacts from permits table
- Links permits to contacts

### 2. Updated: `update_phone_types.py`
- Now queries contacts table instead of permits table
- Updates contacts.phone_validated_at when validated
- Stores line_type and carrier_name in contacts table
- All linked permits automatically get validation data

---

## 📋 How to Run

```bash
# Step 1: Run the migration (IMPORTANT: Do this FIRST!)
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit
python migrate_restructure_contacts.py

# Step 2: Run phone validation (uses new structure)
python update_phone_types.py
```

---

## 🗄️ New Database Schema

### `contacts` Table
```sql
CREATE TABLE contacts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    phone VARCHAR(50) UNIQUE NOT NULL,  -- One phone = one contact
    role VARCHAR(100),
    
    -- Phone validation fields (from Twilio)
    is_mobile BOOLEAN,
    line_type VARCHAR(50),
    carrier_name VARCHAR(255),
    phone_validated_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `permit_contacts` Junction Table
```sql
CREATE TABLE permit_contacts (
    id SERIAL PRIMARY KEY,
    permit_id INTEGER REFERENCES permits(id),
    contact_id INTEGER REFERENCES contacts(id),
    contact_role VARCHAR(100),
    
    UNIQUE(permit_id, contact_id, contact_role)
);
```

---

## 💡 Key Benefits

### Before (Old Way)
```
Phone (555) 123-4567 appears in 20 permits
❌ 20 rows in permits table with same phone
❌ Need to update 20 rows to mark validated
❌ Can't store line_type/carrier_name
```

### After (New Way)
```
Phone (555) 123-4567 appears in 20 permits
✅ 1 row in contacts table
✅ 20 rows in permit_contacts (links)
✅ Update 1 contact = affects all 20 permits
✅ Stores line_type and carrier_name
```

---

## 🔍 Query Examples

### Get all contacts for a permit:
```sql
SELECT c.name, c.phone, c.is_mobile, c.line_type, c.carrier_name, pc.contact_role
FROM permit_contacts pc
JOIN contacts c ON pc.contact_id = c.id
WHERE pc.permit_id = 123;
```

### Get all permits for a contact:
```sql
SELECT p.permit_no, p.address, p.issue_date, pc.contact_role
FROM permit_contacts pc
JOIN permits p ON pc.permit_id = p.id
WHERE pc.contact_id = 456;
```

### Get all mobile numbers:
```sql
SELECT name, phone, carrier_name
FROM contacts
WHERE is_mobile = true;
```

### Get contacts needing validation:
```sql
SELECT phone, name, COUNT(pc.permit_id) as permit_count
FROM contacts c
LEFT JOIN permit_contacts pc ON c.id = pc.contact_id
WHERE c.phone_validated_at IS NULL
GROUP BY c.id, c.phone, c.name
ORDER BY permit_count DESC;
```

---

## ⚠️ Important Notes

### Regarding Permit Renewals:
**Answer: YES, still true!**

The `ON CONFLICT (permit_no) DO NOTHING` clause we added **does NOT affect** how NYC DOB handles renewals.

**NYC DOB behavior:**
- Renewed permits keep the **same permit number**
- The API returns the SAME record with updated fields (status, exp_date)
- Your scraper sees it as an existing permit and skips it

**Your duplicate prevention:**
- Prevents YOUR code from creating duplicates
- Acts as safety net if permit_exists() check fails
- Does NOT change how renewals work

**To capture renewal updates**, you would need to UPDATE existing permits:
```python
# Currently: Skip if exists
if self.permit_exists(permit_no):
    return False

# Better: Update if exists
if self.permit_exists(permit_no):
    self.cursor.execute("""
        UPDATE permits 
        SET permit_status = %s, exp_date = %s
        WHERE permit_no = %s
    """, (status, exp_date, permit_no))
```

---

## 🚀 Deployment Checklist

- [x] Created migration script
- [x] Updated update_phone_types.py
- [ ] Run migration on Railway database
- [ ] Test phone validation script
- [ ] Update dashboard queries to use permit_contacts
- [ ] Commit and push to git
- [ ] Deploy to Railway

---

## 📊 Expected Results

After migration:
- ~10,000-15,000 unique contacts (vs 52,977 duplicate phones before)
- ~65,000+ permit_contacts links
- Phone validation updates 1 row instead of 20
- All queries get validation data automatically via JOIN

Cost savings:
- Before: 52,977 phones × $0.005 = $264.89
- After filtering: ~10,000 phones × $0.005 = $50.00
- Subsequent runs: Only NEW phones validated (~$5-10)
