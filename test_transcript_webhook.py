import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.main import analyze_transcript_for_health_issues

async def test_transcript():
    print("="*60)
    print("🚑 Testing Post-Call Transcript LLM Analysis")
    print("="*60)
    
    transcript = """
    Agent: Hello, how are you feeling today?
    Patient: Honestly, not very well. I have a high fever.
    Agent: I'm sorry to hear that. Do you have any other symptoms?
    Patient: Yes, I'm having severe chest pain and it's hard to breathe.
    Agent: Okay, I have noted that. I will inform your family immediately. Please rest.
    """
    
    print("\nTranscript to analyze:")
    print(transcript)
    
    print("\nSending to Gemini 2.5 Flash...")
    result = await analyze_transcript_for_health_issues(transcript)
    
    print("\n📊 Extracted Data:")
    print(f"   Symptoms: {result['symptoms']}")
    print(f"   Severity: {result['severity']}")
    print(f"   Intent  : {result['intent']}")

if __name__ == "__main__":
    asyncio.run(test_transcript())
