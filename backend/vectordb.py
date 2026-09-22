import chromadb

client = chromadb.PersistentClient(path="./chroma_data")
collection = client.get_or_create_collection("docs")
