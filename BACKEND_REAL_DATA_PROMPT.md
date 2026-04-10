# AutoCritic-EO GIS Backend API Contract - REAL DATA REQUIRED
## For Backend Engineer: Urgent Fix for Mock Data Issue

**🚨 CRITICAL ISSUE:** Backend is running but returning mock/fake data instead of real satellite imagery results.

The AI Orchestrator detects this as mock data because responses look suspiciously generic:
- `check_availability` always returns `{"images_found": 2}` (should vary by location/date)
- `load_imagery` returns `["sequence_1.png", "sequence_2.png", "sequence_3.png"]` (sequential naming)
- `compute_mask` returns `["mask_1.png", "mask_2.png"]` (simple naming)
- `adversarial_optical` returns obviously mock files with anomaly markers

**REQUIRED:** Replace all hardcoded mock responses with real satellite imagery processing pipelines.

---

## 📋 **API ENDPOINTS - REAL DATA SPECIFICATIONS**

### 1. POST `/api/v1/check_availability`

**Request:**
```json
{
  "date": "2024-10-30",
  "location": "Valencia",
  "sensor": "optical"
}
```

**Current Mock Response (BAD):**
```json
{
  "status": "success",
  "data": {
    "images_found": 2  // Always returns 2, regardless of location/date
  }
}
```

**Required Real Response:**
```json
{
  "status": "success",
  "data": {
    "images_found": 15  // Actual count from satellite database query
  }
}
```

**Implementation Requirements:**
- Query real satellite imagery APIs (Sentinel Hub, Planet, USGS, etc.)
- Return actual available image count for the specified date/location/sensor
- Handle zero results when no imagery is available
- Support different sensors (optical, SAR, multispectral)

---

### 2. POST `/api/v1/load_imagery`

**Request:**
```json
{
  "date_range": ["2024-10-29", "2024-10-31"],
  "location": "Valencia",
  "sensor": "optical",
  "bands": "RGB"
}
```

**Current Mock Response (BAD):**
```json
{
  "status": "success",
  "data": {
    "file_list": [
      "sequence_1.png",
      "sequence_2.png",
      "sequence_3.png"
    ]
  }
}
```

**Required Real Response:**
```json
{
  "status": "success",
  "data": {
    "file_list": [
      "sentinel2_20241029_T30SVH_RGB.tif",
      "sentinel2_20241030_T30SVH_RGB.tif",
      "landsat9_20241031_T30SVH_RGB.tif"
    ]
  }
}
```

**Implementation Requirements:**
- Download/process actual satellite imagery from APIs
- Save files with real naming: `{satellite}_{date}_{tile}_{bands}.{ext}`
- Return chronological sequence (earliest to latest dates)
- Support multiple bands (RGB, NIR, SWIR, etc.)
- Files must be accessible at returned paths

---

### 3. POST `/api/v1/compute_mask`

**Request:**
```json
{
  "file_list": [
    "sentinel2_20241029_T30SVH_RGB.tif",
    "sentinel2_20241030_T30SVH_RGB.tif",
    "landsat9_20241031_T30SVH_RGB.tif"
  ],
  "index": "NBR",
  "threshold": -0.2
}
```

**Current Mock Response (BAD):**
```json
{
  "status": "success",
  "data": {
    "computed_masks": [
      "mask_1.png",
      "mask_2.png"
    ],
    "trend_analysis": "Disaster logic expands chronologically over 2 intervals."
  }
}
```

**Required Real Response:**
```json
{
  "status": "success",
  "data": {
    "computed_masks": [
      "nbr_change_20241029_20241030_mask.tif",
      "nbr_change_20241030_20241031_mask.tif"
    ],
    "trend_analysis": "Burned area increased from 15.2 to 23.8 hectares between 2024-10-29 and 2024-10-31, showing active fire progression in Valencia region."
  }
}
```

**Implementation Requirements:**
- Process actual input files from `file_list`
- Compute spectral indices (NBR, NDVI, NDBI, etc.) from real imagery
- Apply threshold for change detection
- Generate real geographical analysis text
- Save computed masks with descriptive names

---

### 4. POST `/api/v1/load_imagery` (with `adversarial=true`)

**Request:**
```json
{
  "date_range": ["2024-10-29", "2024-10-31"],
  "location": "Valencia",
  "adversarial": true
}
```

**Current Mock Response (BAD):**
```json
{
  "status": "success",
  "data": {
    "file_list": [
      "mock_base.png",
      "CLOUD_OBSCURED.png",
      "NODATA_STRIPES.png",
      "NDVI_EXCEEDS_1.png"
    ]
  }
}
```

**Required Real Response:**
```json
{
  "status": "success",
  "data": {
    "file_list": [
      "sentinel2_20241029_T30SVH_cloudy.tif",
      "landsat9_20241030_T30SVH_nodata.tif",
      "sentinel2_20241031_T30SVH_badcal.tif"
    ]
  }
}
```

**Implementation Requirements:**
- Return actual corrupted satellite imagery files
- Include real cloud cover, NoData regions, calibration errors
- Files must contain genuine anomalies that AI can detect
- Used for testing anomaly detection capabilities

---

## 🧪 **VERIFICATION TESTS**

Run these commands to verify real data implementation:

```bash
# Test 1: Check availability varies by location
curl -X POST http://localhost:8000/api/v1/check_availability \
  -H "Content-Type: application/json" \
  -d '{"date": "2024-10-30", "location": "Madrid"}'

# Should return different count than Valencia query

# Test 2: Check real file naming
curl -X POST http://localhost:8000/api/v1/load_imagery \
  -H "Content-Type: application/json" \
  -d '{"date_range": ["2024-10-29", "2024-10-31"], "location": "Barcelona"}'

# Should return files like: sentinel2_20241029_T31TDJ_RGB.tif

# Test 3: Check real processing
curl -X POST http://localhost:8000/api/v1/compute_mask \
  -H "Content-Type: application/json" \
  -d '{"file_list": ["real_file1.tif", "real_file2.tif"], "index": "NDVI"}'

# Should return real analysis, not generic placeholder text
```

---

## ✅ **REAL DATA INDICATORS**

**✅ GOOD (Real Data):**
- File names: `sentinel2_20241029_T30SVH_RGB.tif`
- Dates: Match request parameters exactly
- Tiles: Realistic grid references (T30SVH, T31TDJ)
- Extensions: `.tif`, `.jp2`, `.png` based on actual format
- Counts: Vary by location/date/sensor availability
- Analysis: Describes real geographical features/trends

**❌ BAD (Mock Data - Current Issue):**
- File names: `sequence_1.png`, `mask_1.png`
- Generic: `mock_base.png`, `CLOUD_OBSCURED.png`
- Fixed: Always `{"images_found": 2}`
- Placeholder: "Disaster logic expands chronologically"

---

## 🚀 **IMPLEMENTATION CHECKLIST**

- [ ] Remove all hardcoded responses from backend code
- [ ] Connect to real satellite APIs (Sentinel Hub, Planet Labs, USGS Earth Explorer)
- [ ] Implement actual image download/processing pipelines
- [ ] Generate real spectral index calculations
- [ ] Create genuine change detection masks
- [ ] Return actual geographical analysis
- [ ] Test with multiple locations/dates to verify variability
- [ ] Ensure files are accessible at returned paths

---

## 📞 **CONFIRMATION REQUIRED**

Once you have replaced mock data with real satellite processing:

**Reply with:**
> **"GIS Backend now returns real satellite imagery data from actual processing pipelines. All endpoints use live APIs and return genuine results."**

Then run the AI Orchestrator verification to confirm the system works with real data end-to-end.
