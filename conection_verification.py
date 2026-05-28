from azure.storage.blob import BlobServiceClient

conn_str = "DefaultEndpointsProtocol=https;AccountName=rfpsources;AccountKey="XXXXXXXXX"
container = "rfp-attachments"

client = BlobServiceClient.from_connection_string(conn_str)
# List containers (just to test authentication)
containers = list(client.list_containers())
print("Authentication successful. Containers:", [c.name for c in containers])