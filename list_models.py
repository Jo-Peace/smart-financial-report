import google.generativeai as genai
import os

api_key = "AIzaSyBdElgB6nloHdZBmJGaLDjc-5C92T-OVhc"
genai.configure(api_key=api_key)

print("Listing models...")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)
