#!/usr/bin/env python3
"""
MHub Segmentation Helper

Provides offline-capable model discovery, segment lookups, and workflow generation.
Uses cached data by default; can refresh from network when available.

Usage:
    python mhub_helper.py models [--modality CT|MR|PT] [--segment LIVER]
    python mhub_helper.py model <name>
    python mhub_helper.py segments [--search kidney]
    python mhub_helper.py segment <ID>
    python mhub_helper.py config <model_name> --output custom.yml
    python mhub_helper.py run <model> --input /path/to/nifti --output /path/to/out [--workflow default] [--config custom.yml]
    python mhub_helper.py refresh  # Update cache from network
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from string import Template
from typing import Optional, List, Dict, Any
from datetime import datetime

# Paths relative to this script
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
TEMPLATES_DIR = SCRIPT_DIR.parent / "assets" / "workflow-templates"
MODEL_TEMPLATES_DIR = SCRIPT_DIR.parent / "assets" / "model-templates"
DEFAULT_REPO_ROOT = None  # initialized after helper definitions
REPO_ROOT = None
MODELS_DIR = None

MHUB_API_URL = "https://mhub.ai/api/v2/models/detailed"


def find_default_repo_root() -> Path:
    candidate = Path.cwd()
    for _ in range(20):
        if (candidate / ".git").exists() or (candidate / "models").exists():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return Path.cwd()


def resolve_repo_root(cli_path: Optional[str]) -> Path:
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    env_path = os.environ.get("MHUB_SEGMENTATION_REPO_ROOT")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_REPO_ROOT


def update_repo_root(path: Path):
    global REPO_ROOT, MODELS_DIR
    REPO_ROOT = path
    MODELS_DIR = path / "models"


DEFAULT_REPO_ROOT = find_default_repo_root()
REPO_ROOT = DEFAULT_REPO_ROOT
MODELS_DIR = REPO_ROOT / "models"
 
def load_models_cache() -> Dict[str, Any]:
    """Load cached model data."""
    cache_path = DATA_DIR / "models_summary.json"
    if not cache_path.exists():
        print(f"Error: Cache not found at {cache_path}", file=sys.stderr)
        sys.exit(1)
    with open(cache_path) as f:
        return json.load(f)


def load_segdb_cache() -> Dict[str, Any]:
    """Load cached SegDB data."""
    cache_path = DATA_DIR / "segdb_cache.json"
    if not cache_path.exists():
        print(f"Error: SegDB cache not found at {cache_path}", file=sys.stderr)
        sys.exit(1)
    with open(cache_path) as f:
        return json.load(f)


def list_models(modality: Optional[str] = None, segment: Optional[str] = None, 
                category: Optional[str] = None) -> List[Dict]:
    """List models, optionally filtered by modality, segment, or category."""
    cache = load_models_cache()
    models = cache["models"]
    results = []
    
    for name, info in models.items():
        # Filter by modality
        if modality and modality.upper() not in [m.upper() for m in info["modalities"]]:
            continue
        
        # Filter by segment
        if segment and segment.upper() not in [s.upper() for s in info["segments"]]:
            continue
        
        # Filter by category
        if category and category.lower() not in info["category"].lower():
            continue
        
        results.append({"name": name, **info})
    
    return results


def get_model(name: str) -> Optional[Dict]:
    """Get detailed info for a specific model."""
    cache = load_models_cache()
    return cache["models"].get(name)


def find_models_for_anatomy(anatomy_terms: List[str]) -> List[Dict]:
    """Find models that segment specified anatomical structures."""
    cache = load_models_cache()
    results = []
    
    # Normalize search terms
    terms = [t.upper().replace(" ", "_") for t in anatomy_terms]
    
    for name, info in cache["models"].items():
        segments = [s.upper() for s in info["segments"]]
        matches = [t for t in terms if any(t in s for s in segments)]
        if matches:
            results.append({
                "name": name,
                "matched_terms": matches,
                "matched_segments": [s for s in info["segments"] 
                                    if any(t in s.upper() for t in terms)],
                **info
            })
    
    # Sort by number of matches
    results.sort(key=lambda x: len(x["matched_segments"]), reverse=True)
    return results


def search_segments(query: str) -> List[Dict]:
    """Search SegDB segments by name or ID."""
    cache = load_segdb_cache()
    results = []
    query_upper = query.upper()
    query_lower = query.lower()
    
    for seg_id, info in cache["segments"].items():
        if (query_upper in seg_id or 
            query_lower in info["name"].lower() or
            query_lower in info.get("type_meaning", "").lower()):
            results.append({"id": seg_id, **info})
    
    return results


def get_segment(segment_id: str) -> Optional[Dict]:
    """Get detailed info for a specific segment."""
    cache = load_segdb_cache()
    seg = cache["segments"].get(segment_id.upper())
    if seg:
        return {"id": segment_id.upper(), **seg}
    return None


def get_default_config(model_name: str) -> Optional[str]:
    """Get the default workflow config for a model."""
    config_path = TEMPLATES_DIR / "defaults" / f"{model_name}.yml"
    if config_path.exists():
        return config_path.read_text()
    return None


def render_template(content: str, context: Dict[str, str]) -> str:
    """Render a template string with the provided context."""
    return Template(content).safe_substitute(context)


def scaffold_model(model_id: str,
                   label: str,
                   description: str,
                   category: str,
                   modalities: List[str],
                   primary_modality: str,
                   runner_class: str,
                   input_description: str,
                   output_description: str,
                   license_model: str,
                   license_weights: str,
                   requires_gpu: bool,
                   output_dir: Path,
                   force: bool) -> Path:
    """Create a new model folder populated from templates."""
    if not MODEL_TEMPLATES_DIR.exists():
        raise FileNotFoundError(f"Model templates missing at {MODEL_TEMPLATES_DIR}")

    destination = output_dir / model_id
    if destination.exists():
        if force:
            shutil.rmtree(destination)
        else:
            raise FileExistsError(f"Destination already exists: {destination}")

    destination.mkdir(parents=True, exist_ok=True)

    context = {
        "model_id": model_id,
        "label": label,
        "description": description,
        "category": category,
        "modalities": json.dumps(modalities, ensure_ascii=False),
        "primary_modality": primary_modality,
        "input_description": input_description,
        "output_description": output_description,
        "runner_class": runner_class,
        "license_model": license_model,
        "license_weights": license_weights,
        "requires_gpu": json.dumps(requires_gpu).lower(),
        "docker_image": f"mhubai/{model_id}:latest"
    }

    for template_path in MODEL_TEMPLATES_DIR.rglob("*"):
        if template_path.is_dir():
            continue
        relative_path = template_path.relative_to(MODEL_TEMPLATES_DIR)
        target_path = destination / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_template(template_path.read_text(), context)
        target_path.write_text(rendered)

    return destination


def generate_nifti_config(model_name: str, 
                          file_pattern: str = "flat",
                          modality: str = "ct",
                          output_format: str = "organized") -> str:
    """
    Generate a NIfTI workflow config for a model.
    
    Args:
        model_name: MHub model name
        file_pattern: 'flat', 'subject_folders', 'bids'
        modality: 'ct', 'mr', 'pt'
        output_format: 'organized', 'flat'
    """
    model = get_model(model_name)
    if not model:
        raise ValueError(f"Unknown model: {model_name}")
    
    # Get runner module name from default config
    default_config = get_default_config(model_name)
    runner_module = None
    if default_config:
        for line in default_config.split('\n'):
            line = line.strip()
            if line.startswith('- ') and 'Runner' in line:
                runner_module = line[2:].strip()
                break
    
    if not runner_module:
        runner_module = f"{model_name.title().replace('_', '')}Runner"
    
    # File structure patterns
    structures = {
        "flat": f"re:(.+)\\.nii(\\.gz)?$::$sid@instance@nifti:mod={modality}",
        "subject_folders": f"$sid@instance/re:.*\\.nii(\\.gz)?$@nifti:mod={modality}",
        "bids": f"$sid@instance/anat/re:.*_T1w\\.nii(\\.gz)?$@nifti:mod={modality}"
    }
    
    # Output patterns
    outputs = {
        "organized": [
            "nifti:mod=seg-->[i:sid]/segmentations/[d:roi].nii.gz",
            "nifti:mod=seg-->[i:sid]/combined/[basename]",
            f"nifti:mod={modality}-->[i:sid]/input/[basename]",
            "log-->[i:sid]/logs/[basename]"
        ],
        "flat": [
            "nifti:mod=seg-->[i:sid]_[d:roi].nii.gz"
        ]
    }
    
    config = f'''general:
  data_base_dir: /app/data
  version: 1.0
  description: "{model['label']} on NIfTI files ({file_pattern} structure)"

execute:
- FileStructureImporter
- NiftiConverter
- {runner_module}
- DataOrganizer

modules:
  FileStructureImporter:
    import_id: sid
    structures:
      - {structures.get(file_pattern, structures["flat"])}

  DataOrganizer:
    targets:
'''
    for target in outputs.get(output_format, outputs["organized"]):
        config += f"    - {target}\n"
    
    return config


def generate_dcmqi_config(model_name: str, segments: Optional[List[str]] = None) -> Dict:
    """Generate DCMQI metadata config for DICOM-SEG conversion."""
    model = get_model(model_name)
    if not model:
        raise ValueError(f"Unknown model: {model_name}")
    
    segdb = load_segdb_cache()
    
    # Use model's segments if not specified
    if segments is None:
        segments = model["segments"]
    
    config = {
        "ContentCreatorName": "MHub",
        "ClinicalTrialSeriesID": "0",
        "ClinicalTrialTimePointID": "1",
        "SeriesDescription": model["label"],
        "SeriesNumber": "42",
        "InstanceNumber": "1",
        "BodyPartExamined": "WHOLEBODY",
        "segmentAttributes": [[]]
    }
    
    for i, seg_id in enumerate(segments, 1):
        seg_info = segdb["segments"].get(seg_id, {})
        
        segment = {
            "labelID": i,
            "SegmentDescription": seg_info.get("name", seg_id),
            "SegmentAlgorithmType": "AUTOMATIC",
            "SegmentAlgorithmName": model["label"],
            "SegmentedPropertyCategoryCodeSequence": {
                "CodeValue": seg_info.get("category_code", "123037004"),
                "CodingSchemeDesignator": seg_info.get("category_scheme", "SCT"),
                "CodeMeaning": seg_info.get("category_meaning", "Body structure")
            },
            "SegmentedPropertyTypeCodeSequence": {
                "CodeValue": seg_info.get("type_code", ""),
                "CodingSchemeDesignator": seg_info.get("type_scheme", "SCT"),
                "CodeMeaning": seg_info.get("type_meaning", seg_info.get("name", seg_id))
            },
            "recommendedDisplayRGBValue": seg_info.get("color_rgb", [128, 128, 128])
        }
        
        config["segmentAttributes"][0].append(segment)
    
    return config


def build_model_image(model_id: str,
                      tag: Optional[str] = None,
                      no_cache: bool = False,
                      build_args: Optional[List[str]] = None,
                      platform: Optional[str] = None) -> bool:
    """Build a Docker image from models/<model_id> using the standard Dockerfile."""
    model_dir = MODELS_DIR / model_id
    if not model_dir.is_dir():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")

    docker_tag = tag or f"mhubai/{model_id}:latest"
    command = ["docker", "build", str(model_dir), "-t", docker_tag]

    if platform:
        command += ["--platform", platform]
    if no_cache:
        command.append("--no-cache")
    if build_args:
        for arg in build_args:
            command += ["--build-arg", arg]

    print("Building Docker image:")
    print(f"  {shlex.join(command)}")
    try:
        subprocess.run(command, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Docker build failed ({exc.returncode})")
        return False


def build_docker_run_command(model_name: str,
                             input_dir: str,
                             output_dir: str,
                             workflow: Optional[str] = None,
                             config_file: Optional[str] = None,
                             gpus: Optional[str] = None,
                             network: str = "none",
                             entrypoint: Optional[str] = None,
                             extra_args: Optional[List[str]] = None) -> List[str]:
    """Build the docker run command for a given model."""
    model = get_model(model_name)
    if not model:
        raise ValueError(f"Unknown model: {model_name}")

    input_path = Path(input_dir).expanduser().resolve()
    if not input_path.exists():
        raise ValueError(f"Input path does not exist: {input_path}")
    if not input_path.is_dir():
        raise ValueError(f"Input path must be a directory: {input_path}")

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    config_mount_name = None
    config_path = None
    if config_file:
        config_path = Path(config_file).expanduser().resolve()
        if not config_path.is_file():
            raise ValueError(f"Config file not found: {config_path}")
        config_mount_name = config_path.name

    command = ["docker", "run", "--rm", "-t"]
    if gpus:
        command += ["--gpus", gpus]
    if network:
        command += ["--network", network]

    command += ["-v", f"{input_path}:/app/data/input_data:ro"]
    command += ["-v", f"{output_path}:/app/data/output_data"]

    if config_mount_name and config_path:
        command += ["-v", f"{config_path}:/app/config/{config_mount_name}:ro"]

    if entrypoint:
        command += ["--entrypoint", entrypoint]

    command.append(f"mhubai/{model_name}:latest")

    if workflow:
        command += ["--workflow", workflow]
    elif config_mount_name:
        command += ["--config", f"/app/config/{config_mount_name}"]

    if extra_args:
        command += extra_args

    return command


def run_model_via_docker(model_name: str,
                         input_dir: str,
                         output_dir: str,
                         workflow: Optional[str] = None,
                         config_file: Optional[str] = None,
                         gpus: Optional[str] = None,
                         network: str = "none",
                         entrypoint: Optional[str] = None,
                         extra_args: Optional[List[str]] = None) -> bool:
    """Build and execute the docker command to run a model."""
    command = build_docker_run_command(model_name, input_dir, output_dir,
                                       workflow=workflow,
                                       config_file=config_file,
                                       gpus=gpus,
                                       network=network,
                                       entrypoint=entrypoint,
                                       extra_args=extra_args)
    print("Running Docker command: ")
    print(f"  {shlex.join(command)}")
    try:
        subprocess.run(command, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}")
        return False


def refresh_cache() -> bool:
    """Refresh cache from network. Returns True on success."""
    try:
        import requests
    except ImportError:
        print("requests package not available. Install with: pip install requests")
        return False
    
    try:
        print(f"Fetching from {MHUB_API_URL}...")
        response = requests.get(MHUB_API_URL, timeout=30, headers={
            "accept": "*/*",
            "origin": "https://mhub.ai",
            "referer": "https://mhub.ai/"
        })
        response.raise_for_status()
        
        data = response.json()
        
        # Save raw cache
        raw_cache_path = DATA_DIR / "models_cache.json"
        with open(raw_cache_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        # Rebuild summary
        models = data['data']
        summary = {
            "cache_date": datetime.now().strftime("%Y-%m-%d"),
            "source": MHUB_API_URL,
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
                if mod not in summary["by_modality"]:
                    summary["by_modality"][mod] = []
                summary["by_modality"][mod].append(name)
            
            for seg in segments:
                all_segments.add(seg)
                if seg not in summary["by_segment"]:
                    summary["by_segment"][seg] = []
                summary["by_segment"][seg].append(name)
        
        summary["all_segments"] = sorted(all_segments)
        
        summary_path = DATA_DIR / "models_summary.json"
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"✓ Updated cache with {len(models)} models")
        return True
        
    except Exception as e:
        print(f"✗ Failed to refresh cache: {e}")
        return False


def print_model_table(models: List[Dict]):
    """Print models in a formatted table."""
    if not models:
        print("No models found.")
        return
    
    print(f"{'Model':<30} {'Modality':<10} {'Segments':<10} {'Category':<15}")
    print("-" * 70)
    for m in models:
        name = m.get('name', 'Unknown')[:29]
        modality = ','.join(m.get('modalities', []))[:9]
        seg_count = str(m.get('segment_count', len(m.get('segments', []))))
        category = m.get('category', 'Unknown')[:14]
        print(f"{name:<30} {modality:<10} {seg_count:<10} {category:<15}")


def print_segment_table(segments: List[Dict]):
    """Print segments in a formatted table."""
    if not segments:
        print("No segments found.")
        return
    
    print(f"{'ID':<35} {'Name':<25} {'SNOMED Code':<15}")
    print("-" * 80)
    for s in segments:
        seg_id = s.get('id', 'Unknown')[:34]
        name = s.get('name', '')[:24]
        code = s.get('type_code', '')[:14]
        print(f"{seg_id:<35} {name:<25} {code:<15}")


def main():
    parser = argparse.ArgumentParser(description="MHub Segmentation Helper")
    parser.add_argument("--repo-root", help="Override the repository root used by scaffold/build (env: MHUB_SEGMENTATION_REPO_ROOT)")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # List models
    list_parser = subparsers.add_parser("models", help="List available models")
    list_parser.add_argument("--modality", "-m", help="Filter by modality (CT, MR, PT)")
    list_parser.add_argument("--segment", "-s", help="Filter by segment ID")
    list_parser.add_argument("--category", "-c", help="Filter by category")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # Get model details
    model_parser = subparsers.add_parser("model", help="Get model details")
    model_parser.add_argument("name", help="Model name")
    model_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # Find models for anatomy
    find_parser = subparsers.add_parser("find", help="Find models for anatomy")
    find_parser.add_argument("terms", nargs="+", help="Anatomy terms (e.g., liver kidney)")
    find_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # List/search segments
    seg_parser = subparsers.add_parser("segments", help="Search segments")
    seg_parser.add_argument("--search", "-s", help="Search query")
    seg_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # Get segment details
    seg_detail_parser = subparsers.add_parser("segment", help="Get segment details")
    seg_detail_parser.add_argument("id", help="Segment ID (e.g., LIVER)")
    seg_detail_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # Generate config
    config_parser = subparsers.add_parser("config", help="Generate workflow config")
    config_parser.add_argument("model", help="Model name")
    config_parser.add_argument("--pattern", "-p", default="flat",
                               choices=["flat", "subject_folders", "bids"],
                               help="File organization pattern")
    config_parser.add_argument("--modality", "-m", default="ct",
                               help="Input modality (ct, mr, pt)")
    config_parser.add_argument("--output", "-o", help="Output file path")

    # Run model via Docker
    run_parser = subparsers.add_parser("run", help="Run a model via Docker")
    run_parser.add_argument("model", help="Model name")
    run_parser.add_argument("--input", "-i", required=True, help="Input folder to mount as /app/data/input_data")
    run_parser.add_argument("--output", "-o", required=True, help="Output folder that will receive /app/data/output_data")
    run_parser.add_argument("--workflow", "-w", help="Workflow name inside the container to pass to --workflow")
    run_parser.add_argument("--config", "-c", help="Local workflow config to mount and pass via --config")
    run_parser.add_argument("--gpus", help="Value for --gpus (e.g. all) if you want GPU access")
    run_parser.add_argument("--network", default="none", help="Docker network mode (default: none)")
    run_parser.add_argument("--entrypoint", help="Override the docker entrypoint")
    run_parser.add_argument("--run-arg", "-a", action="append", default=[],
                            help="Additional args appended after the image (repeatable)")
    
    # Generate DCMQI config
    dcmqi_parser = subparsers.add_parser("dcmqi", help="Generate DCMQI config")
    dcmqi_parser.add_argument("model", help="Model name")
    dcmqi_parser.add_argument("--output", "-o", help="Output file path")
    
    # Refresh cache
    subparsers.add_parser("refresh", help="Refresh cache from network")
    
    # Cache info
    subparsers.add_parser("info", help="Show cache info")

    # Scaffold new model package
    scaffold_parser = subparsers.add_parser(
        "scaffold", help="Create a new model package in models/<name> using templates"
    )
    scaffold_parser.add_argument("model_id", help="Identifier for the new model (folder + docker tag)")
    scaffold_parser.add_argument("--label", help="Human label for the model")
    scaffold_parser.add_argument("--description", help="Short description for meta.json")
    scaffold_parser.add_argument("--category", default="Segmentation", help="Primary category")
    scaffold_parser.add_argument("--modalities", default="CT",
                                 help="Comma-separated modalities (default: CT)")
    scaffold_parser.add_argument("--primary-modality", default="CT",
                                 help="Primary modality for inputs")
    scaffold_parser.add_argument("--runner-class", default="CustomRunner",
                                 help="Runner module referenced in config")
    scaffold_parser.add_argument("--input-description", help="Description for the primary input")
    scaffold_parser.add_argument("--output-description", help="Description for the primary prediction")
    scaffold_parser.add_argument("--license-model", default="Apache-2.0",
                                 help="Model license identifier")
    scaffold_parser.add_argument("--license-weights", default="Apache-2.0",
                                 help="Weights license identifier")
    scaffold_parser.add_argument("--requires-gpu", action="store_true",
                                 help="Mark metadata as GPU-required")
    scaffold_parser.add_argument("--output-dir", default="models",
                                 help="Base directory relative to repo root for scaffolded models")
    scaffold_parser.add_argument("--force", action="store_true",
                                 help="Overwrite destination if it already exists")

    # Build model Docker image
    build_parser = subparsers.add_parser("build", help="Build the Docker image for a model in models/<id>")
    build_parser.add_argument("model", help="Model identifier / folder under models/")
    build_parser.add_argument("--tag", help="Docker image tag override (default mhubai/<model>:latest)")
    build_parser.add_argument("--no-cache", action="store_true",
                              help="Pass --no-cache when building the Docker image")
    build_parser.add_argument("--build-arg", "-b", action="append", default=[],
                              help="Optional build arg (KEY=VALUE) to pass to docker build")
    build_parser.add_argument("--platform", help="Target platform to pass to docker build")
    
    args = parser.parse_args()
    repo_root = resolve_repo_root(args.repo_root)
    update_repo_root(repo_root)
    
    if args.command == "models":
        models = list_models(args.modality, args.segment, args.category)
        if args.json:
            print(json.dumps(models, indent=2))
        else:
            print_model_table(models)
    
    elif args.command == "model":
        model = get_model(args.name)
        if model:
            if args.json:
                print(json.dumps(model, indent=2))
            else:
                print(f"Model: {model['label']} ({args.name})")
                print(f"Description: {model['description']}")
                print(f"Modalities: {', '.join(model['modalities'])}")
                print(f"Category: {model['category']}")
                print(f"Segments: {model['segment_count']}")
                print(f"Docker: {model['docker_image']}")
                print(f"License (code): {model['license_code']}")
                print(f"License (weights): {model['license_weights']}")
                if model['cite']:
                    print(f"Citation: {model['cite'][:100]}...")
                print(f"\nSegments: {', '.join(model['segments'][:10])}")
                if len(model['segments']) > 10:
                    print(f"  ... and {len(model['segments']) - 10} more")
        else:
            print(f"Model not found: {args.name}")
            sys.exit(1)
    
    elif args.command == "find":
        models = find_models_for_anatomy(args.terms)
        if args.json:
            print(json.dumps(models, indent=2))
        else:
            if models:
                print(f"Models matching: {', '.join(args.terms)}\n")
                for m in models:
                    print(f"{m['name']}: {', '.join(m['matched_segments'])}")
            else:
                print("No models found for those anatomy terms.")
    
    elif args.command == "segments":
        if args.search:
            segments = search_segments(args.search)
        else:
            cache = load_segdb_cache()
            segments = [{"id": k, **v} for k, v in cache["segments"].items()]
        
        if args.json:
            print(json.dumps(segments, indent=2))
        else:
            print_segment_table(segments)
    
    elif args.command == "segment":
        segment = get_segment(args.id)
        if segment:
            if args.json:
                print(json.dumps(segment, indent=2))
            else:
                print(f"Segment: {segment['id']}")
                print(f"Name: {segment['name']}")
                print(f"Category: {segment['category']}")
                print(f"SNOMED Code: {segment['type_code']}")
                print(f"SNOMED Meaning: {segment['type_meaning']}")
                print(f"Color (RGB): {segment['color_rgb']}")
        else:
            print(f"Segment not found: {args.id}")
            sys.exit(1)
    
    elif args.command == "config":
        try:
            config = generate_nifti_config(args.model, args.pattern, args.modality)
            if args.output:
                Path(args.output).write_text(config)
                print(f"Config written to {args.output}")
            else:
                print(config)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif args.command == "scaffold":
        label = args.label or args.model_id.replace("_", " ").title()
        description = args.description or f"Custom MHub model package for {label}"
        primary_modality = args.primary_modality.upper()
        modalities = [m.strip().upper() for m in args.modalities.split(",") if m.strip()]
        if not modalities:
            modalities = [primary_modality]
        output_dir = Path(args.output_dir)
        if not output_dir.is_absolute():
            output_dir = REPO_ROOT / output_dir
        input_description = args.input_description or f"{primary_modality} image (NIfTI or DICOM folder)."
        output_description = args.output_description or "Segmentation mask produced for the study."
        try:
            destination = scaffold_model(
                args.model_id,
                label,
                description,
                args.category,
                modalities,
                primary_modality,
                args.runner_class,
                input_description,
                output_description,
                args.license_model,
                args.license_weights,
                args.requires_gpu,
                output_dir,
                args.force,
            )
            print(f"Scaffolded new model package at {destination}")
        except Exception as exc:
            print(f"Error: {exc}")
            sys.exit(1)

    elif args.command == "build":
        try:
            success = build_model_image(
                args.model,
                tag=args.tag,
                no_cache=args.no_cache,
                build_args=args.build_arg,
                platform=args.platform,
            )
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        if not success:
            sys.exit(1)
    
    elif args.command == "run":
        try:
            success = run_model_via_docker(
                args.model,
                args.input,
                args.output,
                workflow=args.workflow,
                config_file=args.config,
                gpus=args.gpus,
                network=args.network,
                entrypoint=args.entrypoint,
                extra_args=args.run_arg,
            )
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        if not success:
            sys.exit(1)

    elif args.command == "dcmqi":
        try:
            config = generate_dcmqi_config(args.model)
            output = json.dumps(config, indent=2)
            if args.output:
                Path(args.output).write_text(output)
                print(f"DCMQI config written to {args.output}")
            else:
                print(output)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
    
    elif args.command == "refresh":
        success = refresh_cache()
        sys.exit(0 if success else 1)
    
    elif args.command == "info":
        models_cache = load_models_cache()
        segdb_cache = load_segdb_cache()
        print(f"Models cache:")
        print(f"  Date: {models_cache.get('cache_date', 'Unknown')}")
        print(f"  Models: {models_cache.get('model_count', len(models_cache.get('models', {})))}")
        print(f"  Segments tracked: {len(models_cache.get('all_segments', []))}")
        print(f"\nSegDB cache:")
        print(f"  Date: {segdb_cache.get('cache_date', 'Unknown')}")
        print(f"  Segments: {segdb_cache.get('segment_count', len(segdb_cache.get('segments', {})))}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
