# 3D Slicer on Azure (Windows GPU VM + RDP)

This guide deploys **full 3D Slicer** (with the Slicer Hub extension) on an
Azure **Windows GPU virtual machine**, accessed via Remote Desktop. It complements
[azure-webapp.md](../../HubInterface/hub/azure-webapp.md), which deploys the **Slicer hub** to
Azure App Service (no GPU).

## Architecture

The hub and web viewers run on cheap App Service; Slicer runs on a GPU VM and
connects **outbound** to the hub.

| Component | Where | GPU |
|-----------|--------|-----|
| Slicer Hub + OHIF / VolView / Slim | Azure App Service | No |
| 3D Slicer Image Display client | Windows GPU VM (or local PC) | Optional (viewing) |
| TotalSegmentator / IDC Claude service providers | Same GPU VM (or separate GPU host) | Yes for fast segmentation |

Hub URL example: `https://<app>.azurewebsites.net/api/hub` (`SLICER-HUB-CLOUD` in
Hub settings). Deploy the hub first with [azure-webapp.md](../../HubInterface/hub/azure-webapp.md).

---

## Step 1 — Create the GPU VM

### Azure Portal

1. **Create a resource** → **Virtual machine**
2. **Image:** Windows Server 2022 Datacenter (or Windows 11)
3. **Size:** GPU SKU, for example:
   - **3D viewing + light AI:** `Standard_NV6ads_A10_v5` (~6 GB VRAM partition)
   - **TotalSegmentator on full-body CT:** `Standard_NC4as_T4_v3` or larger (16 GB T4)
4. **Authentication:** password (required for RDP)
5. **Networking:** allow **RDP (3389)** — restrict source to your IP in the NSG
6. Create the VM

### Azure CLI

```bash
RG="rg-slicer-gpu"
LOC="westeurope"
VM="slicer-gpu-01"
USER="azureuser"
PASS='<YourStrongPassword123!>'

az group create -n "$RG" -l "$LOC"

az vm create -g "$RG" -n "$VM" \
  --image Win2022Datacenter \
  --size Standard_NV6ads_A10_v5 \
  --admin-username "$USER" \
  --admin-password "$PASS" \
  --public-ip-sku Standard

az vm open-port -g "$RG" -n "$VM" --port 3389 --priority 1000
```

### GPU sizes not showing in the Portal?

If you see **no N-series at all** (searching `NV`, `NC`, `N` returns nothing), that is
normal on a new subscription. **GPU quota defaults to 0** — Azure often hides the entire
GPU size list until quota is approved. Request quota **first**, then create the VM.

#### Step A — Register Microsoft.Compute (if quotas page shows an error)

If **Usage + quotas** says *“The selected provider is not registered…”*:

**Portal**

1. **Subscriptions** → your subscription
2. **Settings** → **Resource providers** (or search “Resource providers” in the subscription blade)
3. Search **`Microsoft.Compute`**
4. If status is **NotRegistered** → select it → **Register**
5. Wait until status is **Registered** (usually 1–5 minutes; refresh the page)

**Azure CLI**

```bash
az login
az account set --subscription "<your-subscription-name-or-id>"
az provider register --namespace Microsoft.Compute
az provider show --namespace Microsoft.Compute --query registrationState -o tsv
```

Repeat until the command prints `Registered`. Then open **Usage + quotas** again.

Other providers needed later for networking (optional, often auto-register on first VM):
`Microsoft.Network`, `Microsoft.Storage`.

#### Step B — Check subscription type

| Subscription | GPU VMs |
|--------------|---------|
| **Pay-As-You-Go** (with payment method) | Can request GPU quota |
| **Enterprise / CSP** | Can request (IT may need to approve) |
| **Free Trial** ($200 credit) | Often blocked; upgrade to Pay-As-You-Go |
| **Azure for Students** | **Not supported** — 3 vCPU cap; smallest GPU VM needs 6+ vCPUs |

Portal → **Subscriptions** → open yours → note the **Offer** name.

**Students:** upgrade at **Subscriptions** → **Upgrade** (or use a different PAYG subscription).

#### Step C — Request GPU quota (do this before Create VM)

1. Portal → **Subscriptions** → your subscription
2. **Usage + quotas** (left menu)
3. **Provider:** `Microsoft.Compute`
4. **Location:** pick the region you will use (e.g. **West Europe**)
5. In the search/filter box, type **`NV`** or **`NC`**

   Look for rows like:
   - `Standard NVadsA10v5 Family vCPUs`
   - `Standard NCasT4v3 Family vCPUs`
   - `Standard NV Family vCPUs` (older)

6. If **Limit** is **0** → select the row → **Request quota increase**
7. Request at least **6** or **8** vCPUs (enough for one small GPU VM)
8. Submit — approval often takes **15 minutes to 24 hours**

Repeat for each region you might use.

Direct link pattern: Portal search **“Quotas”** → **Compute-VM (cores-vCPUs) subscription limit increases**.

#### Step D — Create VM after quota is approved

1. **Virtual machine** (not App Service, not Container Instances)
2. **Availability options:** **No infrastructure redundancy required** (not a zone)
3. **Size** → **See all sizes** → search **`NV6`** or **`NC4`** (not the word “NVIDIA”)
   - `Standard_NV6ads_A10_v5` — remote desktop / 3D viewing
   - `Standard_NC4as_T4_v3` — TotalSegmentator / CUDA
4. Change **Region** if still empty — try **East US**, **West Europe**, **North Europe**

**Portal checklist (if quota > 0 but still empty)**

1. On **Basics** → **Size**, click **See all sizes** (not the default D-series list).
2. Search **`NVads`**, **`NCas`**, or **`NV6`** — not “N series”.
3. Set **Availability options** to **No infrastructure redundancy required**.
4. Try another **Region**.

**CLI: see what your subscription can use**

```bash
az login
az vm list-skus --location westeurope --size Standard_NV --all --output table
az vm list-skus --location westeurope --size Standard_NC --all --output table
```

Use `--all` so sizes with restrictions still appear. If every row shows
`NotAvailableForSubscription`, open a support ticket for SKU access (quota alone is not enough).

Replace `westeurope` with your region. No rows at all → try another region from
[Products by region — Virtual Machines](https://azure.microsoft.com/explore/global-infrastructure/products-by-region/?products=virtual-machines)
(filter **N-series**).

**If quota is approved but sizes still missing**

- **Help + support** → **New support request** → **Service and subscription limits (quotas)**
  → explain you need **N-series GPU VM** in `<region>` and quota is already > 0.
- Try **Spot** VM pricing tab (some GPU SKUs appear only as Spot when capacity is tight).

---

## Step 2 — Install NVIDIA drivers

**NV-series** VMs (A10, M60, etc.) need **GRID / vGPU** drivers, not CUDA alone.

**Portal:** VM → **Extensions** → Add → Publisher `Microsoft.HpcCompute` →
**NvidiaGpuDriverWindows** (version shown for your OS).

**CLI:**

```bash
az vm extension set -g "$RG" --vm-name "$VM" \
  --name NvidiaGpuDriverWindows \
  --publisher Microsoft.HpcCompute \
  --version 1.6
```

Reboot after the extension completes. In RDP, open **Device Manager** →
**Display adapters** and confirm an NVIDIA device is listed.

---

## Step 3 — Enable GPU rendering for RDP

Without this, Slicer may use software OpenGL and feel slow.

On the VM, run `gpedit.msc` →

**Computer Configuration** → **Administrative Templates** → **Windows Components**
→ **Remote Desktop Services** → **Remote Desktop Session Host** →
**Remote Session Environment**

Enable:

1. **Use hardware graphics adapters for all Remote Desktop Services sessions**
2. **Prioritize H.264/AVC 444 Graphics mode for Remote Desktop connections**
   (sharper 3D; more bandwidth)

Reboot or start a new RDP session.

**RDP client:** enable all display experience options; use GPU acceleration if
your client offers it.

---

## Step 4 — Connect via RDP

1. Portal → VM → **Connect** → **RDP** → download `.rdp` or note the public IP
2. Sign in as `azureuser` (or your admin name) with your password
3. Keep NSG rule **3389** limited to trusted IPs

---

## Step 5 — Install 3D Slicer and extensions

On the VM:

1. Install **3D Slicer** from [https://download.slicer.org/](https://download.slicer.org/)
2. **Extension Manager** → search **Slicer Hub** → Install → restart Slicer
3. For GPU segmentation: install **TotalSegmentator** from Extension Manager

Verify: load a volume and rotate in 3D. For TotalSegmentator, a test job should
log `(gpu, …s)` in the Slicer Python console when segmentation finishes.

---

## Step 6 — Connect Hub to the cloud hub

Replace `<app>` with your App Service name from [azure-webapp.md](../../HubInterface/hub/azure-webapp.md).

### Image Display Client

1. Slicer → **Slicer Hub** → **Image Display Client**
2. Hub: **`SLICER-HUB-CLOUD`** (or set hub URL to
   `https://<app>.azurewebsites.net/api/hub`)
3. Click **Connect**
4. Confirm in hub admin: `https://<app>.azurewebsites.net/api/hub/admin` —
   subscriber name like `3DSlicer-XXXXXX`

### Service Provider (TotalSegmentator, optional)

1. **Service Providers** tab → add row:

   | Field | Value |
   |-------|--------|
   | Product | `TOTALSEG` |
   | Hub | `SLICER-HUB-CLOUD` |
   | onMessage script | `service_providers/products/total_segmentator.py` |

2. Click **Connect**
3. If the hub uses real OAuth (not mock), configure service-provider credentials
   in hub App Settings

No **inbound** ports are required on the VM for Hub — only outbound HTTPS and
WebSocket to the hub.

See [totalsegmentator-readme.md](../Resources/scripts/totalsegmentator-readme.md)
for event and status-update behavior.

---

## Step 7 — Smoke test

1. Open worklist:
   `https://<app>.azurewebsites.net/worklist-client/`
2. Open a study → Slicer on the GPU VM receives `imagingstudy-open`
3. From VolView or OHIF on the hub, send a study to TotalSegmentator → GPU
   inference on the VM → result returns via the hub

---

## VM sizing and cost

| Workload | Suggested SKU | VRAM |
|----------|---------------|------|
| Viewing + Hub only | `NV6ads_A10_v5` | ~6 GB partition |
| TotalSegmentator full-body CT | `NC4as_T4_v3` / `NC8as_T4_v3` | 16 GB |
| Heavy multi-job | `NCads_A100_v4` | 40–80 GB |

GPU VMs bill while **running**. Use Portal → VM → **Auto-shutdown**, or
**deallocate** when idle.

---

## Security

- NSG: RDP **only from known IPs**
- Strong VM password; consider **Azure Bastion** instead of public RDP long term
- Hub: use production OAuth, not mock endpoints, when exposed
- Service-provider keys: hub App Settings, not baked into the VM image

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No GPU sizes in VM wizard | Register `Microsoft.Compute`; request NV/NC quota (Steps A–C); disable availability zones |
| Black or slow 3D in Slicer | RDP GPU policies (Step 3); NVIDIA driver in Device Manager |
| TotalSegmentator uses CPU | Install CUDA-capable stack per TotalSegmentator extension; run `nvidia-smi` in CMD |
| Hub will not connect | Allow outbound HTTPS to `*.azurewebsites.net`; verify hub URL; set `HUB_WS_KEEPALIVE=true` on App Service |
| RDP drops | Use stable network or Azure Bastion |

---

## Related docs

- [azure-webapp.md](../../HubInterface/hub/azure-webapp.md) — Slicer hub on Azure App Service
- [totalsegmentator-readme.md](../Resources/scripts/totalsegmentator-readme.md) — service provider setup
- [HUB-README.md](../../HubInterface/hub/docs/HUB-README.md) — hub endpoints and `SLICER-HUB-CLOUD`
