Conclave is a multi-agent system framework that allows users to build, and observe
multi-agent systems in user friendly manner.

The central premise is to make it easier for the non technical people to build multi-agent
systems and automations.

## Competitive lanscape

Few products that are used to build multi-agent systems

- LangGragh
- OpenAI Agents SDK
- Microsoft AutoGen
- CrewAI

 
I ran an example in each of them. The purpose was the create the recommendation report for the VP of product, with the constraint that only one of the feature of the two could ship, with specifications, like effort and unblocked deals etc. 
The example used 4 different agents: researcher, writer, analyst and reviewer. Finally a human gate to approve the plan.

All of them were simple enough to implement in a single main.py, with their own constructs, such as handoff and terminal conditions. I am yet to explore the UI provided by any of them, which I am going to research next