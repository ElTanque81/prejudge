import sys
from openminion import Agent

with Agent(instructions="Summarize the text in three bullet points.") as agent:
    print(agent.run(open(sys.argv[1]).read()).text)
