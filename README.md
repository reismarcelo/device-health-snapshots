# Health snapshots package

## Initial setup

    services health-samples
     setup
      max-samples   3
      stop-on-error true
     !
     assessment device-up
      command 1
       run    "show platform"
       parse  "^\d+(?:/[A-Z0-9]+)+[ \t]+[^ \t]+[ \t]+(.+?)[ \t]+[^ \t]+$"
       expect "(IOS XR RUN|OK)"
      !
     !
     assessment pe-check
      command 1
       run    "show platform"
       parse  "^\d+(?:/[A-Z0-9]+)+[ \t]+[^ \t]+[ \t]+(.+?)[ \t]+[^ \t]+$"
       expect "(IOS XR RUN|OK)"
      !
      command 2
       run    "show ospf neighbor"
       parse  "^\d+(?:\.\d+){3}[ \t]+\d+[ \t]+(\w+)"
       expect FULL
      !
     !
     device A3-ASR9K-R6
      assessment             pe-check
      lightweight-assessment device-up
     !
    !


## Running the different actions

    admin@ncs# services health-samples device A3-ASR9K-R6 run
    success Assessment completed without errors
    
    admin@ncs# services health-samples device A3-ASR9K-R6 run
    success Assessment completed without errors
    
    admin@ncs# services health-samples device A3-ASR9K-R6 diff
    success Diff passed
    
    admin@ncs# services health-samples device A3-ASR9K-R6 clear
    success Cleared all assessments from device



