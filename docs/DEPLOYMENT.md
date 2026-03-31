# GateKeep - Azure Deployment Guide

## Prerequisites

- Azure CLI installed and authenticated
- Azure Container Registry (ACR) created
- Azure Kubernetes Service (AKS) cluster or Azure Container Apps environment
- Azure Blob Storage account
- Azure PostgreSQL (Flexible Server) - or use in-cluster
- Microsoft Entra ID app registration with OIDC configured

## Option 1: Docker Compose (Dev / Small Firm)

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your Azure credentials

# 2. Start all services
docker compose up -d

# 3. Verify
docker compose ps
curl http://localhost:8000/health
```

## Option 2: Azure Container Apps (Medium Firm)

```bash
# 1. Create resource group
az group create --name gatekeep-rg --location eastus2

# 2. Create Container Apps environment
az containerapp env create \
  --name gatekeep-env \
  --resource-group gatekeep-rg \
  --location eastus2

# 3. Build and push images
az acr build --registry $ACR_NAME --image gatekeep-web:latest .
az acr build --registry $ACR_NAME --image gatekeep-worker:latest -f Dockerfile.worker .

# 4. Create PostgreSQL Flexible Server
az postgres flexible-server create \
  --name gatekeep-db \
  --resource-group gatekeep-rg \
  --admin-user gatekeep \
  --admin-password "$POSTGRES_PASSWORD" \
  --sku-name Standard_B1ms \
  --tier Burstable

# 5. Create Blob Storage
az storage account create \
  --name gatekeepstorage \
  --resource-group gatekeep-rg \
  --sku Standard_LRS \
  --kind StorageV2

# 6. Deploy Container App
az containerapp create \
  --name gatekeep-web \
  --resource-group gatekeep-rg \
  --environment gatekeep-env \
  --image $ACR_NAME.azurecr.io/gatekeep-web:latest \
  --target-port 8000 \
  --ingress external \
  --env-vars \
    DATABASE_URL="postgresql+asyncpg://gatekeep:$POSTGRES_PASSWORD@gatekeep-db.postgres.database.azure.com:5432/gatekeep" \
    AZURE_STORAGE_CONNECTION_STRING="$CONN_STRING" \
    ENTRA_TENANT_ID="$TENANT_ID" \
    ENTRA_CLIENT_ID="$CLIENT_ID" \
    ENTRA_CLIENT_SECRET="$CLIENT_SECRET"
```

## Option 3: AKS (Large Firm / Multi-tenant)

```bash
# 1. Create AKS cluster
az aks create \
  --resource-group gatekeep-rg \
  --name gatekeep-aks \
  --node-count 3 \
  --node-vm-size Standard_D4s_v3 \
  --enable-addons monitoring \
  --generate-ssh-keys

# 2. Get credentials
az aks get-credentials --resource-group gatekeep-rg --name gatekeep-aks

# 3. Create namespace and secrets
kubectl apply -f k8s/namespace.yaml

kubectl create secret generic gatekeep-secrets \
  --namespace gatekeep \
  --from-literal=database-url="postgresql+asyncpg://gatekeep:$PASS@host:5432/gatekeep" \
  --from-literal=elastic-password="$ELASTIC_PASSWORD" \
  --from-literal=redis-url="redis://:$REDIS_PASSWORD@redis:6379/0" \
  --from-literal=azure-storage-connection="$CONN_STRING" \
  --from-literal=entra-tenant-id="$TENANT_ID" \
  --from-literal=entra-client-id="$CLIENT_ID" \
  --from-literal=entra-client-secret="$CLIENT_SECRET" \
  --from-literal=jwt-secret="$JWT_SECRET"

# 4. Apply manifests
kubectl apply -f k8s/
kubectl apply -f k8s/web/
kubectl apply -f k8s/worker/
kubectl apply -f k8s/ingress/

# 5. Verify
kubectl get pods -n gatekeep
kubectl get ingress -n gatekeep
```

## Microsoft Entra ID Configuration

1. Go to Azure Portal > Microsoft Entra ID > App registrations
2. Create new registration
3. Set redirect URI: `https://gatekeep.yourfirm.com/auth/callback`
4. Note: Tenant ID, Client ID
5. Create client secret under "Certificates & secrets"
6. (Optional) Assign API permissions for User.Read

## Azure Blob Storage Setup

```bash
# Create storage account
az storage account create \
  --name gatekeepartifacts \
  --resource-group gatekeep-rg \
  --sku Standard_RAGRS \
  --kind StorageV2

# Create container
az storage container create \
  --name gatekeep-artifacts \
  --account-name gatekeepartifacts

# Get connection string
az storage account show-connection-string \
  --name gatekeepartifacts \
  --resource-group gatekeep-rg
```

## Network Security (VNET)

```bash
# Create VNET
az network vnet create \
  --resource-group gatekeep-rg \
  --name gatekeep-vnet \
  --address-prefix 10.0.0.0/16

# Create subnets
az network vnet subnet create \
  --resource-group gatekeep-rg \
  --vnet-name gatekeep-vnet \
  --name aks-subnet \
  --address-prefix 10.0.1.0/24

az network vnet subnet create \
  --resource-group gatekeep-rg \
  --vnet-name gatekeep-vnet \
  --name db-subnet \
  --address-prefix 10.0.2.0/24 \
  --service-endpoints Microsoft.Storage Microsoft.Sql

# Private endpoint for Blob Storage
az network private-endpoint create \
  --name gatekeep-blob-pe \
  --resource-group gatekeep-rg \
  --vnet-name gatekeep-vnet \
  --subnet db-subnet \
  --private-connection-resource-id "/subscriptions/$SUB_ID/resourceGroups/gatekeep-rg/providers/Microsoft.Storage/storageAccounts/gatekeepartifacts" \
  --group-id blob \
  --connection-name gatekeep-blob-conn
```

## Backup Strategy

```bash
# Run the backup script
./scripts/backup.sh

# Or set up Azure Backup for PostgreSQL
az postgres flexible-server backup create \
  --resource-group gatekeep-rg \
  --name gatekeep-db

# Set up automated backups (daily)
az postgres flexible-server update \
  --resource-group gatekeep-rg \
  --name gatekeep-db \
  --backup-retention 35
```

## Monitoring

- **Flower Dashboard**: `http://localhost:5555` (Celery task monitoring)
- **Elasticsearch**: `http://localhost:9200/_cluster/health`
- **Azure Monitor**: Enable for AKS/Container Apps
- **Application Insights**: Add instrumentation key to environment

## Troubleshooting

```bash
# Check pod logs
kubectl logs -n gatekeep -l app=gatekeep-web --tail=100
kubectl logs -n gatekeep -l app=gatekeep-worker --tail=100

# Check database connectivity
kubectl exec -n gatekeep deployment/gatekeep-web -- python -c "
import asyncio
from src.models.database import engine
async def test():
    async with engine.connect() as conn:
        result = await conn.execute('SELECT 1')
        print('DB OK:', result.scalar())
asyncio.run(test())
"

# Check Elasticsearch
kubectl exec -n gatekeep deployment/gatekeep-web -- python -c "
import asyncio
from elasticsearch import AsyncElasticsearch
async def test():
    es = AsyncElasticsearch('http://elasticsearch:9200', basic_auth=('elastic', 'password'))
    print(await es.cluster.health())
asyncio.run(test())
"