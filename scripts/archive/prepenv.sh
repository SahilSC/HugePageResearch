#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/<org>/KernMLOps.git"
REPO_DIR="KernMLOps"
CONTAINER_NAME="kernmlops"

echo "==== Step 2: Run setup script ===="
source scripts/setup_prep_env.sh

echo "==== Step 3: Start docker container ===="
make docker INTERACTIVE="d" CONTAINER_CMD="sleep infinity" CONTAINER_OPTS="--name $CONTAINER_NAME"

echo "Waiting for container to start..."
sleep 5

echo "==== Step 4: Install YCSB inside container ===="
docker exec -it $CONTAINER_NAME bash -c "cd /KernMLOps && make install-ycsb"

echo "==== Step 5: Setup Redis inside container ===="
docker exec -it $CONTAINER_NAME bash -c "cd /KernMLOps && make setup-redis"

echo "==== Step 6: Disable ASLR (host) ===="
echo 0 | sudo tee /proc/sys/kernel/randomize_va_space
echo "kernel.randomize_va_space = 0" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

echo "==== Step 7: Run baseline benchmark inside container ===="
docker exec -it $CONTAINER_NAME bash -c "
cd /KernMLOps
source .venv/bin/activate
python python/kernmlops collect -v \
  -c config/redis_always.yaml \
  --benchmark redis
"

echo "==== Done ===="
