import os
import json
import time
import re
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from dotenv import load_dotenv
from openai import OpenAI
from groq import Groq
from .Context_module import VectorStorage

# Load environment variables
load_dotenv()

try:
    from .logger import get_logger
except ImportError:
    from agent.logger import get_logger

logger = get_logger("Parser")

class Parser:
    def __init__(self):
        # Initialize OpenAI (kept for backward compatibility if needed)
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if self.openai_api_key:
            self.client = OpenAI()
        
        # Initialize Groq
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        if not self.groq_api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")
        self.groq_client = Groq(api_key=self.groq_api_key)

        # Initialize vector storage for RAG
        self.vector_storage = VectorStorage()

        # Define valid game objects
        self.game_objects = {
            "player", "heart", "hearts", "bomb", "bombs", "fire", "fires", "treasure", 
            "maze", "wall", "walls", "mini-map", "health", "space", "w", "s", "a", "d",
            "route", "game", "corner", "position", "control", "controls", "key", "keys",
            "user", "health_points", "damage", "score", "lives"
        }
        
        # Get project root directory
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.experience_file = os.path.join(self.project_root, "api_files", "experience_description.txt")

    def read_experience_description(self) -> str:
        """Read the experience description file."""
        try:
            with open(self.experience_file, 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.warning(f"Experience description file not found at {self.experience_file}")
            return ""
        except Exception as e:
            logger.error(f"Error reading experience description: {str(e)}")
            return ""

    def generate_plan(self) -> Dict:
        """Generate a plan based on experience_description.txt."""
        # Read experience description
        experience_text = self.read_experience_description()
        
        if not experience_text:
            raise ValueError("Experience description file is empty or not found. Cannot generate plan.")
        
        # Get relevant context from game rules and environment
        game_rules_context = self.vector_storage.query_similar_chunks(experience_text, namespace='game_rules')
        game_env_context = self.vector_storage.query_similar_chunks(experience_text, namespace='game_environment')
        
        # Format contexts
        rules_context = "\n".join([f"Game Rule {i+1}: {c['text']}" for i, c in enumerate(game_rules_context)])
        env_context = "\n".join([f"Game Environment {i+1}: {c['text']}" for i, c in enumerate(game_env_context)])
        
        messages = [
            {"role": "system", "content": """You are a game environment configuration generator that creates plans for setting up the game environment based on experience descriptions.
             The plan is for CONFIGURING THE GAME ENVIRONMENT, not for players. Generate instructions for placing objects, setting up the maze, and dynamically modifying the environment.
             
             Generate a plan in the following JSON format:
             {
                 "sequence": [
                     {
                         "step": 1,
                         "action": "environment setup action to perform",
                         "object_type": "type of object",
                         "count": number of objects to create,
                         "placement": "description of where/how to place objects",
                         "condition": "condition that must be met to complete this step"
                     },
                     ...
                 ],
                 "loop": {
                     "monitor": ["list of game states to continuously monitor"],
                     "conditions": [
                         {
                             "if": "condition to check",
                             "then": "action to take",
                             "object_type": "type of object to add/modify",
                             "count": number of objects,
                             "placement": "description of placement logic"
                         },
                         ...
                     ]
                 }
             }
             
             Rules:
             1. Sequence contains steps to set up the initial game environment (walls, objects, etc.)
             2. Each step should specify what objects to create, how many, and where to place them
             3. Loop contains conditions that should be actively monitored during gameplay to dynamically modify the environment
             4. Monitor list should include game states relevant to environment changes (e.g., "player_position", "treasure_position", "player_treasure_distance")
             5. Conditions should describe when and how to modify the environment (e.g., spawn fires when player is near treasure)
             6. The object_type should be one of the following: "wall", "bomb", "heart", "fire", "treasure", "watergun"
             7. Use clear, technical language for environment configuration
             8. Return valid JSON only"""},
            {"role": "user", "content": f"""Game Rules Context:
            {rules_context}
            
            Game Environment Context:
            {env_context}
            
            Experience Description:
            {experience_text}
            
            Generate an environment configuration plan that describes how to set up the game environment to match this experience. The plan should specify where to place objects, how many of each, and what conditions to monitor for dynamic environment changes."""}
        ]

        # Retry logic for invalid JSON responses
        max_retries = 3
        retry_delay = 2.0  # seconds
        
        for attempt in range(max_retries):
            try:
                # Call Groq API with streaming
                completion = self.groq_client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=messages,
                    temperature=0,
                    max_completion_tokens=2048,
                    top_p=1,
                    stream=True,
                    stop=None
                )
                
                # Collect streaming response
                content = ""
                for chunk in completion:
                    if chunk.choices and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'content') and delta.content:
                            content += delta.content
                
                # Wait a bit to ensure streaming is complete
                time.sleep(0.1)
                
                content = content.strip()
                
                # Check if content is empty
                if not content:
                    raise ValueError("Empty response from Groq API")
                
                # Try to extract JSON from the response (in case there's extra text)
                # First, try to find JSON object in code blocks (```json ... ``` or ``` ... ```)
                code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if code_block_match:
                    # Extract JSON from code block
                    content = code_block_match.group(1).strip()
                else:
                    # Look for JSON object - find the first { and try to match to the last }
                    start_idx = content.find('{')
                    if start_idx != -1:
                        # Try to find matching closing brace
                        brace_count = 0
                        end_idx = start_idx
                        for i in range(start_idx, len(content)):
                            if content[i] == '{':
                                brace_count += 1
                            elif content[i] == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    end_idx = i + 1
                                    break
                        if brace_count == 0:
                            content = content[start_idx:end_idx]
                        else:
                            # Incomplete JSON - raise error to trigger retry
                            raise ValueError("Incomplete JSON response")
                    else:
                        # No JSON object found
                        raise ValueError("No JSON object found in response")
                
                # Parse the response into a plan dictionary
                plan_json = json.loads(content)
                return plan_json
                
            except (ValueError, json.JSONDecodeError) as e:
                # If JSON parsing fails, check if this is the last attempt
                if attempt < max_retries - 1:
                    logger.warning(f"Invalid JSON response (attempt {attempt + 1}/{max_retries}). Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                else:
                    # Last attempt failed, raise error
                    logger.error(f"Failed to parse JSON after {max_retries} attempts. Error: {str(e)}")
                    raise ValueError(f"Invalid JSON response from Groq API after {max_retries} attempts: {str(e)}")
            except Exception as e:
                logger.error(f"Error generating plan: {str(e)}")
                raise

    

    def parse_prompt(self, prompt: str) -> Dict:
        """Parse a prompt into steps with end conditions using enhanced RAG."""
        # Get relevant context from both game rules and environment
        game_rules_context = self.vector_storage.query_similar_chunks(prompt, namespace='game_rules')
        game_env_context = self.vector_storage.query_similar_chunks(prompt, namespace='game_environment')
        
        # Get completion from OpenAI with combined context
        completion = self.get_completion(prompt, game_rules_context, game_env_context)
        
        try:
            # Parse the JSON response
            parsed_steps = json.loads(completion)
            if not isinstance(parsed_steps, list):
                parsed_steps = [parsed_steps]
            
            # Validate steps
            validated_steps = []
            for step in parsed_steps:
                # Only validate that the object exists and required fields are present
                if (step.get("object") in self.game_objects and 
                    isinstance(step.get("action"), str) and 
                    step.get("action").strip()):
                    validated_steps.append(step)
            
            # Create the result structure without context
            result = {
                "steps": validated_steps,
                "total_steps": len(validated_steps)
            }
            
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing completion as JSON: {str(e)}")
            return {
                "steps": [],
                "total_steps": 0
            }


if __name__ == "__main__":
    """Test the parser module by generating a plan from experience_description.txt."""
    parser = Parser()
    
    logger.info("Generating plan from experience_description.txt...")

    # Generate plan based on experience description
    plan = parser.generate_plan()

    logger.info(f"Generated Plan:\n{json.dumps(plan, indent=2)}")

    # Save plan to api_files/plan.json
    plan_file = os.path.join(parser.project_root, "api_files", "plan.json")
    try:
        with open(plan_file, 'w') as f:
            json.dump(plan, f, indent=2)
        logger.info(f"Plan saved successfully to {plan_file}")
    except Exception as e:
        logger.error(f"Error saving plan to file: {str(e)}")