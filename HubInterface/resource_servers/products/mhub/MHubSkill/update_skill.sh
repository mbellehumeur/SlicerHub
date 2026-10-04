#!/bin/bash
# update_skill.sh - Update all MHub skill caches
#
# Usage: ./update_skill.sh
#
# This script updates:
# - Model list from MHub API
# - Default workflow configs from GitHub
# - SegDB segment database
#
# Requirements:
# - curl
# - Python 3.8+
# - pip install requests segdb

set -e
cd "$(dirname "$0")"

echo "╔════════════════════════════════════════════╗"
echo "║  MHub Segmentation Skill - Cache Update    ║"
echo "╚════════════════════════════════════════════╝"
echo ""

# Check dependencies
command -v curl >/dev/null 2>&1 || { echo "Error: curl required"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Error: python3 required"; exit 1; }

# 1. Models cache
echo "┌─ Step 1: Fetching MHub models from API..."
curl -s "https://mhub.ai/api/v2/models/detailed" \
  -H "accept: */*" \
  -H "origin: https://mhub.ai" \
  -H "referer: https://mhub.ai/" \
  > data/models_cache.json

MODEL_COUNT=$(python3 -c "import json; print(len(json.load(open('data/models_cache.json'))['data']))")
echo "│  ✓ Fetched $MODEL_COUNT models"

# 2. Rebuild summary
echo "├─ Step 2: Rebuilding model index..."
python3 scripts/mhub_helper.py refresh 2>/dev/null || python3 << 'EOF'
import json
from datetime import datetime

with open('data/models_cache.json') as f:
    raw = json.load(f)

models = raw['data']
summary = {
    "cache_date": datetime.now().strftime("%Y-%m-%d"),
    "source": "https://mhub.ai/api/v2/models/detailed",
    "model_count": len(models),
    "models": {},
    "by_modality": {},
    "by_segment": {},
    "all_segments": []
}

all_segments = set()
for m in models:
    name = m['name']
    segments = m.get('segmentations', [])
    modalities = m.get('modalities', [])
    
    summary["models"][name] = {
        "label": m.get('label', name),
        "description": m.get('description', ''),
        "modalities": modalities,
        "segments": segments,
        "segment_count": len(segments),
        "predictions": m.get('predictions', []),
        "category": m.get('categories', ['Unknown'])[0],
        "license_code": m.get('licence', {}).get('model', 'Unknown'),
        "license_weights": m.get('licence', {}).get('weights', 'Unknown'),
        "cite": m.get('cite', ''),
        "inputs": m.get('inputs', []),
        "docker_image": f"mhubai/{name}:latest"
    }
    
    for mod in modalities:
        summary["by_modality"].setdefault(mod, []).append(name)
    for seg in segments:
        all_segments.add(seg)
        summary["by_segment"].setdefault(seg, []).append(name)

summary["all_segments"] = sorted(all_segments)

with open('data/models_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
print("   Rebuilt index")
EOF
echo "│  ✓ Index rebuilt"

# 3. Fetch default configs
echo "├─ Step 3: Fetching default workflow configs..."
CONFIG_COUNT=0
python3 << 'PYEOF'
import json
import subprocess
from pathlib import Path

with open('data/models_summary.json') as f:
    models = json.load(f)['models']

config_dir = Path('assets/workflow-templates/defaults')
config_dir.mkdir(parents=True, exist_ok=True)

count = 0
for name in models:
    url = f'https://raw.githubusercontent.com/MHubAI/models/main/models/{name}/config/default.yml'
    result = subprocess.run(['curl', '-s', '-f', url], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        (config_dir / f'{name}.yml').write_text(result.stdout)
        count += 1

print(count)
PYEOF
echo "│  ✓ Fetched configs for $MODEL_COUNT models"

# 4. Update SegDB
echo "├─ Step 4: Updating SegDB cache..."

# Try to install/upgrade segdb
pip install -q segdb --upgrade --break-system-packages 2>/dev/null || \
pip install -q segdb --upgrade 2>/dev/null || \
echo "│  ⚠ Could not upgrade segdb, using existing version"

python3 << 'PYEOF'
import json
from datetime import datetime

try:
    from segdb.lookup import db
    
    types_dict = db.types.to_dict('index')
    categories_dict = db.categories.to_dict('index')
    segments = {}
    
    for seg_id, row in db.segmentations.iterrows():
        color_parts = [int(c) for c in str(row.get('color', '128,128,128')).split(',')]
        anat_region = row.get('anatomic_region', '')
        type_info = types_dict.get(anat_region, {})
        cat_id = row.get('category', '')
        cat_info = categories_dict.get(cat_id, {})
        
        segments[seg_id] = {
            'name': row.get('name', seg_id),
            'category': cat_id,
            'category_code': cat_info.get('CodeValue', ''),
            'category_scheme': cat_info.get('CodingSchemeDesignator', ''),
            'category_meaning': cat_info.get('CodeMeaning', ''),
            'type_id': anat_region,
            'type_code': type_info.get('CodeValue', ''),
            'type_scheme': type_info.get('CodingSchemeDesignator', ''),
            'type_meaning': type_info.get('CodeMeaning', ''),
            'modifier': row.get('modifier', None),
            'color_rgb': color_parts
        }
    
    with open('data/segdb_cache.json', 'w') as f:
        json.dump({
            'cache_date': datetime.now().strftime('%Y-%m-%d'),
            'source': 'segdb Python package',
            'segment_count': len(segments),
            'segments': segments,
            'categories': db.categories.to_dict('index'),
            'modifiers': db.modifiers.to_dict('index')
        }, f, indent=2, default=str)
    
    print(f"SEGDB_OK:{len(segments)}")
except ImportError:
    print("SEGDB_SKIP:segdb not installed")
except Exception as e:
    print(f"SEGDB_ERR:{e}")
PYEOF

echo "│  ✓ SegDB cache updated"

# 5. Summary
echo "└─ Step 5: Verification"
echo ""
python3 scripts/mhub_helper.py info

# Get today's date for reminder
TODAY=$(date +%Y-%m-%d)
echo ""
echo "╔════════════════════════════════════════════╗"
echo "║  Update Complete!                          ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Update cache date in SKILL.md to: $TODAY"
echo "  2. Update cache date in README.md to: $TODAY"
echo "  3. Test: python scripts/mhub_helper.py models"
echo "  4. Rebuild ZIP: zip -r mhub-segmentation.zip ."
echo ""
