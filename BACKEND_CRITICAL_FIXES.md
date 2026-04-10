# AutoCritic-EO Backend - Critical GEE Integration Fixes

## 🚨 **REMAINING ISSUES IDENTIFIED**

After the latest test run, the backend still has critical issues:

### 1. **Date Range Parsing Still Broken**
**Error:** `GEE Exception: Collection.toList: Empty date ranges not supported for the current operation.`

**Problem:** AI sends `"date_range": "2024-10-30"` but GEE still can't parse it.

### 2. **File List Parameter Issue**
**Error:** `GEE Exception: Image.load: Image asset 'T' not found`

**Problem:** `compute_mask` receives `"file_list": "output_from_T2"` (string) but expects array of file paths.

**AI Request:**
```json
{
  "file_list": "output_from_T2"  // This is a STRING, not an array!
}
```

**Expected:**
```json
{
  "file_list": ["sentinel2_20241029_T30SVH.tif", "sentinel2_20241030_T30SVH.tif"]
}
```

### 3. **Mock Data Still Returned**
**Problem:** `compute_mask` returns bizarre mock data:
```json
{
  "computed_masks": [
    "mask_o", "mask_u", "mask_t", "mask_p", "mask_u", "mask_t", "mask__", ...
  ]
}
```
This looks like it's parsing "output_from_T2" character-by-character!

---

## 🔧 **REQUIRED FIXES**

### **Fix 1: Proper Date Range Parsing**

**Current Issue:** Single date strings not converted to GEE date ranges.

**Required Implementation:**
```python
from datetime import datetime, timedelta

def parse_date_range(date_param):
    """Convert AI date parameter to proper GEE date range."""
    if isinstance(date_param, str):
        # Single date: "2024-10-30" -> expand to range
        try:
            date_obj = datetime.fromisoformat(date_param)
            start_date = (date_obj - timedelta(days=1)).strftime('%Y-%m-%d')
            end_date = (date_obj + timedelta(days=1)).strftime('%Y-%m-%d')
            return start_date, end_date
        except ValueError:
            # Fallback for invalid date format
            return "2024-10-29", "2024-10-31"
    elif isinstance(date_param, list) and len(date_param) == 2:
        # Already a range
        return date_param[0], date_param[1]
    else:
        # Default range
        return "2024-10-29", "2024-10-31"

# In your GEE code:
start_date, end_date = parse_date_range(request.date_range)

collection = ee.ImageCollection('COPERNICUS/S2') \
    .filterBounds(geometry) \
    .filterDate(start_date, end_date) \
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
```

### **Fix 2: Handle File List Parameter Correctly**

**Problem:** AI sends `"file_list": "output_from_T2"` but this is a **reference to previous vertex output**, not actual file paths.

**Solution:** The backend needs to understand that `"output_from_T2"` means "use the output from vertex T2".

**Implementation:**
```python
def resolve_file_list(file_list_param, vertex_dependencies):
    """
    Resolve file list references to actual file paths.
    AI sends references like "output_from_T2" or "T2_output"
    """
    if isinstance(file_list_param, str):
        # Check if it's a reference to previous vertex
        if file_list_param.startswith("output_from_"):
            vertex_id = file_list_param.replace("output_from_", "")
            # Look up actual files from previous vertex execution
            # This requires maintaining state between vertex executions
            return get_files_from_vertex(vertex_id)
        elif file_list_param.startswith("T") and "_output" in file_list_param:
            vertex_id = file_list_param.split("_")[0]
            return get_files_from_vertex(vertex_id)
        else:
            # Single file path
            return [file_list_param]
    elif isinstance(file_list_param, list):
        # Already a list of files
        return file_list_param
    else:
        return []
```

### **Fix 3: Implement Real GEE compute_mask**

**Current:** Returns mock character-parsing results.

**Required:** Real spectral index computation and change detection.

**GEE Implementation:**
```javascript
function computeNBR(image) {
  var nir = image.select('B8');
  var swir = image.select('B12');
  var nbr = nir.subtract(swir).divide(nir.add(swir));
  return image.addBands(nbr.rename('NBR'));
}

function computeMask(collection, threshold) {
  var images = collection.toList(collection.size());
  var masks = [];
  var analyses = [];

  for (var i = 0; i < images.size().subtract(1).getInfo(); i++) {
    var img1 = ee.Image(images.get(i));
    var img2 = ee.Image(images.get(i + 1));

    // Compute NBR for both images
    var nbr1 = computeNBR(img1).select('NBR');
    var nbr2 = computeNBR(img2).select('NBR');

    // Change detection
    var changeMask = nbr2.subtract(nbr1).lt(threshold);

    // Export mask
    var maskName = 'nbr_change_' + i + '_' + (i+1);
    masks.push(maskName);

    // Simple trend analysis
    var meanChange = changeMask.reduceRegion({
      reducer: ee.Reducer.mean(),
      geometry: geometry,
      scale: 10
    }).get('NBR');

    analyses.push('Change detected between images ' + (i+1) + '-' + (i+2));
  }

  return {
    computed_masks: masks,
    trend_analysis: analyses.join('; ')
  };
}
```

### **Fix 4: Proper Vertex Dependency Handling**

**Problem:** AI orchestrator sends vertex references, backend needs to resolve them.

**Solution:** Maintain execution state between vertices:

```python
# Global or session state
vertex_outputs = {}

def execute_vertex(vertex_id, tool_name, params):
    # Resolve file_list references
    if 'file_list' in params:
        params['file_list'] = resolve_file_references(params['file_list'], vertex_outputs)

    # Execute tool
    result = execute_tool(tool_name, params)

    # Store output for future vertices
    vertex_outputs[vertex_id] = result.get('data', {})

    return result
```

---

## 🧪 **TEST CASES**

### Test 1: Date Range
```bash
curl -X POST http://localhost:8000/api/v1/load_imagery \
  -H "Content-Type: application/json" \
  -d '{"date_range": "2024-10-30", "location": "Valencia"}'
# Should NOT return "Empty date ranges" error
```

### Test 2: File List Resolution
```bash
curl -X POST http://localhost:8000/api/v1/compute_mask \
  -H "Content-Type: application/json" \
  -d '{"file_list": ["real_file1.tif", "real_file2.tif"], "index": "NBR"}'
# Should NOT try to load 'r', 'e', 'a', 'l' as separate assets
```

### Test 3: Real Processing
```bash
# Should return real GEE results, not character-parsed mock data
curl -X POST http://localhost:8000/api/v1/compute_mask \
  -H "Content-Type: application/json" \
  -d '{"file_list": ["sentinel_asset_1", "sentinel_asset_2"], "index": "NBR"}'
```

---

## ✅ **SUCCESS CRITERIA**

- [ ] No "Empty date ranges" GEE errors
- [ ] No "Image asset 'T' not found" errors (single characters)
- [ ] `compute_mask` returns real spectral analysis, not character arrays
- [ ] File list references properly resolved to actual file paths
- [ ] All responses are real GEE data or proper errors

---

## 📞 **CONFIRMATION**

Once all fixes are implemented, reply with:
> **"Backend now properly parses dates, resolves file references, and returns real GEE-computed spectral analysis without character-parsing artifacts."**

Then the AI Orchestrator will have a fully functional real data pipeline!