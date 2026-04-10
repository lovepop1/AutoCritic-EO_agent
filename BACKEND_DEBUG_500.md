# AutoCritic-EO Backend - Debug 500 Internal Server Error

## ✅ **EXCELLENT PROGRESS MADE!**

The backend is now working much better:
- ✅ Date ranges properly parsed: `"2024-10-29 to 2024-10-31"`
- ✅ File references recognized: `"output_from_T2"` → proper error messages
- ✅ Real error handling: HTTP 500 instead of mock data
- ✅ AI correctly handles empty data scenarios

## 🚨 **REMAINING ISSUE: 500 Internal Server Error**

**Error:** `500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/load_imagery`

**Problem:** The backend server is crashing when processing `load_imagery` requests.

---

## 🔧 **DEBUGGING STEPS**

### **Step 1: Check Backend Logs**

Look at your backend server logs for the stack trace. The 500 error means there's a Python exception in your code.

Common causes:
- GEE authentication issues
- Invalid geometry/AOI parsing
- Date range format problems
- Missing error handling in GEE calls

### **Step 2: Test Endpoint Directly**

```bash
curl -X POST http://localhost:8000/api/v1/load_imagery \
  -H "Content-Type: application/json" \
  -d '{
    "date_range": "2024-10-29 to 2024-10-31",
    "location": "Valencia"
  }'
```

**Expected Response:** Either success with real data, or proper error message (not 500).

### **Step 3: Check GEE Authentication**

Verify your GEE service account credentials:
```python
import ee
ee.Initialize()  # Should not throw authentication errors
```

### **Step 4: Add Error Logging**

Add comprehensive error logging to your load_imagery endpoint:

```python
import logging
logger = logging.getLogger(__name__)

@app.post("/api/v1/load_imagery")
def load_imagery(request):
    try:
        logger.info(f"Processing load_imagery: {request}")

        # Parse date range
        start_date, end_date = parse_gee_date_range(request.date_range)
        logger.info(f"Date range: {start_date} to {end_date}")

        # Parse location/geometry
        geometry = parse_location(request.location)
        logger.info(f"Geometry: {geometry}")

        # GEE collection
        collection = ee.ImageCollection('COPERNICUS/S2') \
            .filterBounds(geometry) \
            .filterDate(start_date, end_date) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))

        # Check if collection is empty
        count = collection.size().getInfo()
        logger.info(f"Images found: {count}")

        if count == 0:
            return {
                "status": "success",
                "data": {"file_list": []}
            }

        # Process images...
        # Add more logging here

    except Exception as e:
        logger.error(f"load_imagery failed: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"GEE processing failed: {str(e)}"
        }
```

### **Step 5: Common Fixes**

**Issue: Empty collection**
```python
# Check collection size before processing
count = collection.size().getInfo()
if count == 0:
    return {"status": "success", "data": {"file_list": []}}
```

**Issue: Invalid geometry**
```python
def parse_location(location_str):
    # Convert location name to GEE geometry
    # Use geocoding service or predefined regions
    if location_str == "Valencia":
        return ee.Geometry.Rectangle([-0.5, 39.2, -0.2, 39.6])  # Valencia bounds
    # Add more locations...
```

**Issue: Date format**
```python
# Ensure dates are in YYYY-MM-DD format
start_date, end_date = parse_gee_date_range(request.date_range)
# Validate: should be strings like "2024-10-29"
```

---

## 🧪 **TEST CASES**

### Test 1: Direct Endpoint Test
```bash
curl -X POST http://localhost:8000/api/v1/load_imagery \
  -H "Content-Type: application/json" \
  -d '{"date_range": "2024-10-29 to 2024-10-31", "location": "Valencia"}'
# Should return 200 with data or proper error, not 500
```

### Test 2: Simple Date Range
```bash
curl -X POST http://localhost:8000/api/v1/load_imagery \
  -H "Content-Type: application/json" \
  -d '{"date_range": "2024-10-30", "location": "Valencia"}'
# Test single date parsing
```

### Test 3: Full AI Pipeline
```bash
python verify_orchestrator.py
# Should complete without 500 errors
```

---

## ✅ **SUCCESS CRITERIA**

- [ ] No 500 Internal Server Errors
- [ ] `load_imagery` returns real satellite data or proper error messages
- [ ] `compute_mask` receives file arrays and performs real change detection
- [ ] AI Orchestrator processes real multi-temporal satellite imagery

---

## 📞 **CONFIRMATION**

Once the 500 error is fixed and real data flows, reply with:
> **"Backend debugged - no more 500 errors. Real satellite data now flows through the complete AI Orchestrator pipeline."**

The AutoCritic-EO system will then be fully operational! 🚀