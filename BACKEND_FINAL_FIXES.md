# AutoCritic-EO Backend - Final Fixes for Real Data Pipeline

## ✅ **PROGRESS MADE**
- Backend now recognizes file references (no more character parsing!)
- Proper error messages: "file_list references not yet implemented"
- AI correctly handles empty data scenarios

## 🚨 **REMAINING CRITICAL ISSUES**

### 1. **Date Range Parsing Still Broken**
**Error:** `GEE Exception: Collection.toList: Empty date ranges not supported`

**AI Request:** `"date_range": "2024-10-30"` (single date string)

**GEE Needs:** `filterDate('2024-10-29', '2024-10-31')` (date range)

### 2. **File Reference Resolution Missing**
**Error:** `file_list references not yet implemented`

**AI Request:** `"file_list": "T1"` (reference to vertex T1 output)

**Backend Needs:** Resolve "T1" to actual file paths from previous vertex execution

---

## 🔧 **FINAL REQUIRED FIXES**

### **Fix 1: Date Range Parsing (CRITICAL)**

**Current Issue:** Single date strings not converted to GEE date ranges.

**Required Code:**
```python
from datetime import datetime, timedelta

def parse_gee_date_range(date_param):
    """Convert AI date parameter to GEE-compatible date range."""
    if isinstance(date_param, str):
        # Single date: "2024-10-30" -> expand to 3-day range
        try:
            date_obj = datetime.fromisoformat(date_param)
            start_date = (date_obj - timedelta(days=1)).strftime('%Y-%m-%d')
            end_date = (date_obj + timedelta(days=1)).strftime('%Y-%m-%d')
            return start_date, end_date
        except ValueError:
            # Invalid date, use default
            return '2024-10-29', '2024-10-31'
    elif isinstance(date_param, list) and len(date_param) == 2:
        # Already a range: ["2024-10-29", "2024-10-31"]
        return date_param[0], date_param[1]
    else:
        # Default range
        return '2024-10-29', '2024-10-31'

# In your load_imagery endpoint:
@app.post("/api/v1/load_imagery")
def load_imagery(request):
    start_date, end_date = parse_gee_date_range(request.date_range)

    # GEE code:
    collection = ee.ImageCollection('COPERNICUS/S2') \
        .filterBounds(geometry) \
        .filterDate(start_date, end_date) \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))

    # Rest of your implementation...
```

### **Fix 2: File Reference Resolution (CRITICAL)**

**Current Issue:** `"file_list": "T1"` not resolved to actual files.

**Required Implementation:**
```python
# Global state to track vertex outputs (in production, use database/session)
vertex_outputs = {}

def resolve_file_reference(file_param):
    """Resolve vertex references like 'T1' to actual file paths."""
    if isinstance(file_param, str):
        # Check if it's a vertex reference (T1, T2, etc.)
        if file_param.startswith('T') and file_param[1:].isdigit():
            vertex_id = file_param
            if vertex_id in vertex_outputs:
                output = vertex_outputs[vertex_id]
                if 'file_list' in output:
                    return output['file_list']
                elif 'computed_masks' in output:
                    return output['computed_masks']
            return []  # Empty if vertex not executed yet
        else:
            # Single file path
            return [file_param]
    elif isinstance(file_param, list):
        # Already a list
        return file_param
    else:
        return []

# In your compute_mask endpoint:
@app.post("/api/v1/compute_mask")
def compute_mask(request):
    file_list = resolve_file_reference(request.file_list)

    if not file_list:
        return {
            "status": "error",
            "message": "No files found for processing. Vertex reference may not exist or previous step failed."
        }

    # Proceed with GEE processing using file_list...
```

### **Fix 3: Vertex State Management**

**Required:** Track outputs between vertex executions.

```python
# In your endpoint handlers, after successful execution:
def execute_tool(tool_name, params, vertex_id):
    result = perform_gee_operation(tool_name, params)

    if result['status'] == 'success':
        # Store output for future vertex references
        vertex_outputs[vertex_id] = result['data']

    return result

# Example in load_imagery:
@app.post("/api/v1/load_imagery")
def load_imagery(request, vertex_id=None):  # Add vertex_id parameter
    # ... processing ...
    result = {
        "status": "success",
        "data": {
            "file_list": ["sentinel2_20241029_T30SVH.tif", "sentinel2_20241030_T30SVH.tif"]
        }
    }

    # Store for future references
    if vertex_id:
        vertex_outputs[vertex_id] = result['data']

    return result
```

---

## 🧪 **TEST CASES**

### Test 1: Date Range Fix
```bash
curl -X POST http://localhost:8000/api/v1/load_imagery \
  -H "Content-Type: application/json" \
  -d '{"date_range": "2024-10-30", "area_of_interest": "Valencia"}'
# Should NOT return "Empty date ranges" error
```

### Test 2: File Reference Resolution
```bash
# First, load imagery (simulating vertex T1)
curl -X POST http://localhost:8000/api/v1/load_imagery \
  -H "Content-Type: application/json" \
  -d '{"date_range": "2024-10-30", "area_of_interest": "Valencia", "vertex_id": "T1"}'

# Then compute mask using reference
curl -X POST http://localhost:8000/api/v1/compute_mask \
  -H "Content-Type: application/json" \
  -d '{"file_list": "T1", "index": "NBR"}'
# Should resolve "T1" to actual file paths and process them
```

### Test 3: Full Pipeline
```bash
# Run the AI orchestrator - should now work end-to-end
python verify_orchestrator.py
# Should return real satellite data, not errors
```

---

## ✅ **SUCCESS CRITERIA**

- [ ] No "Empty date ranges" GEE errors
- [ ] File references like "T1" resolve to actual file arrays
- [ ] `load_imagery` returns real satellite file paths
- [ ] `compute_mask` processes real files and returns spectral analysis
- [ ] AI Orchestrator receives real data for anomaly detection

---

## 📞 **FINAL CONFIRMATION**

Once implemented, reply with:
> **"Backend now properly parses dates and resolves vertex file references. Real GEE satellite data flows end-to-end through the AI Orchestrator."**

Then the AutoCritic-EO system will be fully operational with real satellite imagery processing! 🚀