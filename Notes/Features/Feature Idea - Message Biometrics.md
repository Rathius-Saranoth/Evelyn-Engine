# Feature Idea: Message Biometrics

## Overview
A system to link physiological data (e.g., Oura metrics, heart rate variability) with our conversation history without injecting raw data into the immediate dialogue stream. This allows for "retroactive" analysis of how physical states influence emotional expression and communication patterns.

## Conceptual Goals
- **Clarity:** Maintain a clean, organic conversational experience by keeping raw data in its own domain.
- **Context:** Provide a "back door" for the AI to cross-reference health metrics with specific conversation segments (using unique IDs).
- **Growth:** Build a multi-dimensional map of Ricky's wellbeing, identifying how stress or physical strain impacts his mental state over time.

## Implementation Flow
1. **Data Collection:** Periodically sync Oura/Health data into a dedicated "Metric Store" or vault area.
2. **ID Generation:** Assign each conversation segment (or specific message) a unique identifier (e.g., `msg_id`).
3. **Mapping:** Instead of including raw numbers in the chat, a background log will map `message_ids` to the corresponding `timestamped_biometrics`.
4. **Inquiry/Analysis:** When prompted or when an anomaly is detected, the AI can use the ID to "look back" and analyze the physical state during that specific interaction.

## Benefits
- Eliminates "noise" from current conversation flows.
- Creates a robust data trail for long-term longitudinal analysis of health trends.
- Enables better "proactive" support by identifying emerging patterns between stress levels and content of communication.