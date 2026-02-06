import os
import openai
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec
from typing import List, Dict
import json
from dotenv import load_dotenv
from tqdm import tqdm
import glob
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,  # Changed from DEBUG to INFO
    format='%(message)s'  # Simplified format to only show message
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class VectorStorage:
    def __init__(self):
        try:
            # Initialize OpenAI
            self.openai_api_key = os.getenv("OPENAI_API_KEY")
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY not found in environment variables")
            self.client = OpenAI()

            # Initialize Pinecone
            self.pinecone_api_key = os.getenv("PINECONE_API_KEY")
            if not self.pinecone_api_key:
                raise ValueError("PINECONE_API_KEY not found in environment variables")
            
            # Configure Pinecone client
            self.pc = Pinecone(api_key=self.pinecone_api_key)
            self.index_name = "firefighter-game"
            
            # Create index if it doesn't exist
            try:
                self.index = self.pc.Index(name=self.index_name)
            except Exception as e:
                self.pc.create_index(
                    name=self.index_name,
                    dimension=1536,
                    metric="cosine",
                    spec=ServerlessSpec(
                        cloud="aws",
                        region="us-east-1"
                    )
                )
                self.index = self.pc.Index(name=self.index_name)
        except Exception as e:
            logger.error(f"Error in initialization: {str(e)}")
            raise

    def read_context_file(self, file_path: str) -> str:
        """Read a context file."""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()
                return content
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            raise
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {str(e)}")
            raise

    def get_context_files(self) -> List[str]:
        """Get all context files from the context directory."""
        context_dir = "context"
        
        if not os.path.exists(context_dir):
            logger.error(f"Context directory not found: {context_dir}")
            raise FileNotFoundError(f"Context directory not found: {context_dir}")
        
        # Get all .txt files in the context directory
        files = glob.glob(os.path.join(context_dir, "*.txt"))
        return files

    def chunk_text(self, text: str, chunk_size: int = 1000) -> List[str]:
        """Split text into chunks of approximately equal size."""
        try:
            words = text.split()
            chunks = []
            current_chunk = []
            current_size = 0

            for word in words:
                current_chunk.append(word)
                current_size += len(word) + 1
                if current_size >= chunk_size:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_size = 0

            if current_chunk:
                chunks.append(" ".join(current_chunk))

            return chunks
        except Exception as e:
            logger.error(f"Error chunking text: {str(e)}")
            raise

    def get_embedding(self, text: str) -> List[float]:
        """Get embedding for a text using OpenAI's API."""
        try:
            response = self.client.embeddings.create(
                model="text-embedding-ada-002",
                input=[text]
            )
            embedding = response.data[0].embedding
            return embedding
        except Exception as e:
            logger.error(f"Error getting embedding: {str(e)}")
            raise

    def store_embeddings(self):
        """Process all context files and store embeddings in Pinecone."""
        try:
            # Get all context files
            context_files = self.get_context_files()
            
            for file_path in context_files:
                # Get namespace from filename (without extension)
                namespace = os.path.splitext(os.path.basename(file_path))[0]
                
                # Read and chunk the text
                text = self.read_context_file(file_path)
                chunks = self.chunk_text(text)

                # Process chunks in batches
                batch_size = 100
                for start in range(0, len(chunks), batch_size):
                    batch = chunks[start:start + batch_size]
                    vectors = []
                    
                    for i, chunk in enumerate(batch):
                        try:
                            # Get embedding
                            embedding = self.get_embedding(chunk)
                            
                            # Prepare vector data with namespace
                            vectors.append({
                                "id": f"{namespace}_chunk_{start + i}",
                                "values": embedding,
                                "metadata": {
                                    "text": chunk,
                                    "chunk_index": start + i,
                                    "total_chunks": len(chunks),
                                    "source_file": file_path
                                }
                            })
                        except Exception as e:
                            logger.error(f"Error processing chunk {i} in batch starting at {start}: {str(e)}")
                            continue
                    
                    if vectors:
                        try:
                            # Upsert batch to Pinecone with namespace
                            self.index.upsert(vectors=vectors, namespace=namespace)
                        except Exception as e:
                            logger.error(f"Error upserting batch to Pinecone: {str(e)}")

                logger.info(f"✓ Successfully processed: {file_path}")

            return True

        except Exception as e:
            logger.error(f"Error storing embeddings: {str(e)}")
            return False

    def query_similar_chunks(self, query: str, top_k: int = 3, namespace: str = None) -> List[Dict]:
        """Query similar chunks based on text input."""
        try:
            # Get embedding for query
            query_embedding = self.get_embedding(query)
            
            # Query Pinecone
            results = self.index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace
            )

            # Format results
            formatted_results = []
            for match in results.matches:
                try:
                    formatted_results.append({
                        "text": match.metadata.get("text", ""),
                        "score": match.score,
                        "chunk_index": match.metadata.get("chunk_index", 0),
                        "source_file": match.metadata.get("source_file", "")
                    })
                except Exception as e:
                    logger.error(f"Error formatting match: {str(e)}")
                    continue

            return formatted_results

        except Exception as e:
            logger.error(f"Error querying similar chunks: {str(e)}")
            return []

    def get_index_stats(self):
        """Get statistics about the Pinecone index."""
        try:
            stats = self.index.describe_index_stats()
            return stats
        except Exception as e:
            logger.error(f"Error getting index stats: {str(e)}")
            return None

    def delete_embeddings(self, namespace: str = None):
        """Delete embeddings from Pinecone index.
        Args:
            namespace (str, optional): Specific namespace to delete. If None, deletes all vectors from all namespaces.
        Returns:
            bool: True if deletion was successful, False otherwise.
        """
        try:
            if namespace:
                # Delete all vectors in the specified namespace
                self.index.delete(delete_all=True, namespace=namespace)
                logger.info(f"✓ Successfully deleted all vectors in namespace: {namespace}")
            else:
                # Delete all vectors in all namespaces
                self.index.delete(delete_all=True)
                logger.info("✓ Successfully deleted all vectors from all namespaces")
            return True
        except Exception as e:
            logger.error(f"Error deleting embeddings: {str(e)}")
            return False

def main():
    """Main function to demonstrate usage."""
    try:
        # Initialize vector storage
        storage = VectorStorage()
        
        # Get initial index stats
        initial_stats = storage.get_index_stats()
        print(f"\nInitial vectors in index: {initial_stats.total_vector_count}")

        storage.delete_embeddings(namespace="event_logging")
        storage.delete_embeddings(namespace="game_environment")

        
        # Example: Delete vectors from a specific namespace
        # storage.delete_embeddings(namespace="mechanics")
        
        # Example: Delete all vectors
        # storage.delete_embeddings()
        
        # Store embeddings
        storage.store_embeddings()
        
        # Get updated index stats
        final_stats = storage.get_index_stats()
        print(f"Final vectors in index: {final_stats.total_vector_count}")

    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise

if __name__ == "__main__":
    main() 