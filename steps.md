This is a list of steps I need to do to aid in my research of replay_trace. Ideally I need to see a difference between huge pages and regular pages


- Run a "regular program" with only system_trace to see time for redis. I'm referring to the program that runs the relevant config by tewari.
    - Observe if the benchmark keeps track of the load and run phase
        - If it keeps track of the load phase, I need to separately create a timer for only the run phase. 
    - Then create a timer to compare this regular program to my replay trace and see if they replay at the same frequency
- create background program to issue breaks