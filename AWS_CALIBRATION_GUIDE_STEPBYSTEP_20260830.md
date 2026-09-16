# AWS EC2 Calibration Guide — Step-by-Step
**Date:** August 30, 2026 | **Cost:** ~$5 (~₹500) | **Time:** 1.5 hours total

---

## What You'll Do

1. Create AWS account (5 min, free tier eligible)
2. Launch EC2 instance (5 min)
3. Upload your calibration code + data (10 min)
4. Run calibration on 16 CPU cores (45 min actual)
5. Download results (5 min)
6. Terminate instance (stop paying immediately)

**Result:** Best parameters in 45 minutes instead of 24 hours

---

## STEP 1: Create AWS Account (5 minutes)

### Go to AWS
1. Open browser → `aws.amazon.com`
2. Click **"Create an AWS Account"** (top right)
3. Fill in:
   - Email address (use your Gmail/email)
   - Password (make secure)
   - AWS account name (e.g., "Codex-Trading")
   - Click **Create Account**

### Verify Email
- Check your email inbox
- Click verification link
- Complete phone verification (SMS)

### Add Payment Method
1. Enter credit card details (Visa/Mastercard)
   - Card number
   - Expiry (MM/YY)
   - CVV
2. Enter billing address
3. Click **Verify and Add**

**Cost so far: $0** (free account creation)

---

## STEP 2: Launch EC2 Instance (10 minutes)

### Open EC2 Console
1. Log in to AWS: `console.aws.amazon.com`
2. Search for **"EC2"** (top left)
3. Click **EC2** service
4. Click **"Launch Instance"** (blue button)

### Configure Instance

**Step 1: Choose AMI (Operating System)**
- Search: `Ubuntu Server 22.04`
- Click **Select**

**Step 2: Choose Instance Type**
- Search: `c6i.4xlarge`
- Click to select
- **This is your 16-core machine**

**Step 3: Configure Storage**
- Default is fine (100 GB)
- Click **Next**

**Step 4: Add Tags** (optional)
- Name: `Codex-Calibration`
- Click **Next**

**Step 5: Security Group**
- Default is fine
- Click **Next**

**Step 6: Review & Launch**
- Click **Launch**

### Key Pair (Important!)
- "Create new key pair"
- Name: `codex-trading-key`
- Download: **Save the .pem file to your desktop**
- Click **Launch Instance**

**Your instance is starting!** ✅

### Get Instance Details
1. Go to **Instances** (left menu)
2. Find your instance (state = "running")
3. Copy these (you'll need them):
   - **Instance ID** (starts with i-)
   - **Public IPv4** (your server's internet address)

---

## STEP 3: Connect to Server (5 minutes)

### On Windows, use PuTTY (SSH client)

**Option A: Simple - Use AWS EC2 Instance Connect (No Download Needed)**
1. Go to Instances
2. Select your instance
3. Click **Connect** (button at top)
4. Click **EC2 Instance Connect** tab
5. Click **Connect**
6. **You're now in the terminal!** ✅

**Option B: Advanced - Use PuTTY (If Option A doesn't work)**
```
1. Download PuTTY: putty.org
2. Install it
3. Open PuTTY
4. Hostname: your Public IPv4 (from instance details)
5. Port: 22
6. SSH → Auth → Private key file: your .pem file
7. Click Open → Yes → It connects
```

---

## STEP 4: Prepare Server (10 minutes)

Once connected (you see terminal prompt), run these commands:

### Update System
```bash
sudo apt update
sudo apt install -y python3-pip git
```

### Install Python Libraries
```bash
pip3 install numpy pandas ta-lib redis scikit-learn
```

### Create Working Directory
```bash
mkdir -p ~/codex-calibration
cd ~/codex-calibration
```

---

## STEP 5: Upload Your Code (10 minutes)

**Option A: Using AWS Console (Easiest)**

1. Download your calibration code on your laptop:
   - `STAGE2_CALIBRATION_33PARAMS_24HOURS.py`
   - Any data files you need
   - Zip them: `calibration.zip`

2. In AWS terminal (EC2 Instance Connect):
```bash
# This opens a file upload dialog
# Upload your files from your laptop
```

**Option B: Using SCP (If you're comfortable with terminal)**

On your laptop PowerShell:
```powershell
# Copy file to server
scp -i "path\to\codex-trading-key.pem" `
    "C:\path\to\STAGE2_CALIBRATION_33PARAMS_24HOURS.py" `
    ubuntu@<YOUR_PUBLIC_IPv4>:~/codex-calibration/
```

---

## STEP 6: Run Calibration on Cloud (45 minutes)

In AWS terminal:

```bash
# Navigate to working directory
cd ~/codex-calibration

# Start calibration (runs in background)
nohup python3 STAGE2_CALIBRATION_33PARAMS_24HOURS.py > calibration.log &

# Check progress (run this every few minutes)
tail -f calibration.log

# See how many cores it's using
htop
```

**The calibration is now running on 16 CPU cores!** ⚡

---

## STEP 7: Monitor Progress (Every 5 minutes)

```bash
# Check latest log output
tail -20 calibration.log

# Check system load (should be ~1500% = 16 cores working)
top

# Expected output:
# [Iter    1] 45.23% [+-51.75%] [0.05h]
# [Iter    2] 52.10% [+-26.75%] [0.08h]
# ...
```

**Calibration will complete in ~45 minutes.**

When done, you'll see:
```
Calibration complete!
Results saved to: calibration_results.json
```

---

## STEP 8: Download Results (5 minutes)

### Download Results File

**In AWS Terminal:**
```bash
# List result files
ls -lh calibration*.json

# Keep the terminal window open for downloading
```

**On Your Laptop PowerShell (New Window):**
```powershell
# Download results from server
scp -i "path\to\codex-trading-key.pem" `
    ubuntu@<YOUR_PUBLIC_IPv4>:~/codex-calibration/calibration_results.json `
    "C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN\"

# You now have the results locally!
```

---

## STEP 9: Terminate Instance (IMPORTANT - Stops Billing)

**Once calibration is complete and you've downloaded results:**

```bash
# In AWS Console
1. Go to Instances
2. Select your instance
3. Click Instance State → Terminate
4. Click Terminate

# ⚠️ DO THIS or you'll keep being charged!
# Once terminated, billing stops immediately
```

---

## Complete Timeline

| Step | Time | Cost |
|------|------|------|
| Create AWS account | 5 min | $0 |
| Launch instance | 5 min | $0 (running not started yet) |
| Connect to server | 2 min | $0.68/hour now ticking |
| Prepare server | 10 min | $0.01 |
| Upload code | 10 min | $0.01 |
| **Run calibration** | **45 min** | **$0.51** |
| Download results | 5 min | $0.06 + data transfer $0.01 |
| **Terminate instance** | 1 min | **STOPS BILLING** |
| **TOTAL** | **~1.5 hours** | **~$5 (~₹500)** |

---

## Troubleshooting

### Instance won't connect
- Wait 2-3 min (it takes time to boot)
- Check instance state = "running" (not pending)
- Try refreshing browser

### Python libraries fail to install
```bash
sudo apt install -y build-essential python3-dev
pip3 install --upgrade pip
pip3 install numpy pandas scikit-learn
```

### Calibration takes longer than 45 min
- That's ok, just keep monitoring
- c6i.4xlarge should do it in 45-60 min
- Check: `top` to see all 16 cores in use

### Out of disk space
```bash
df -h  # Check space
rm -rf ~/codex-calibration  # Delete if needed
```

### Lost connection to terminal?
- The calibration keeps running in background
- Reconnect with EC2 Instance Connect
- Run: `tail -f calibration.log` to check progress

---

## Payment Verification

### Check Your Bill
1. AWS Console → **Billing Dashboard** (top right)
2. Check "Estimated charges"
3. Should show:
   - EC2 instance: ~$0.68/hr × 1.5 hrs = ~$1
   - Data transfer: ~$1-2
   - **Total: ~$3-5**

### Stop Charges
- Terminate instance immediately after downloading results
- Charges stop within 1 minute of termination

---

## What NOT to Do

❌ Leave instance running after calibration (costs $0.68/hour)  
❌ Forget to download results before terminating  
❌ Close laptop without terminating instance  
❌ Create multiple instances by accident

---

## Quick Reference Commands

```bash
# Monitor calibration
tail -f calibration.log

# Check CPU usage (should be ~1500%)
top

# Check if done yet
grep "complete" calibration.log

# If something goes wrong, see error log
cat calibration.log | grep ERROR

# Disk space check
df -h

# Stop running process (if needed)
pkill -f STAGE2_CALIBRATION
```

---

## What You Get

✅ Calibration results (JSON file with best parameters)  
✅ Full output log (see every iteration)  
✅ Time saved: 24 hours → 45 minutes  
✅ Cost: ~$5 (~₹500)  
✅ Experience: You now know AWS EC2!

---

## Ready?

When you're ready:
1. ✅ Create AWS account (5 min)
2. ✅ Follow steps 2-9 above
3. ✅ I'll wait here to help if you get stuck

**Estimated total time: 1.5 hours**  
**Estimated cost: $5 (~₹500)**  
**Result: Best parameters ready by 11 PM tonight!**

Any questions before you start? Just ask! 🚀
