#!/usr/bin/env python3
import sys
import getopt
import os
import yaml
import json
import datetime
import time
import subprocess

sys.path.append('../aux/')
from aux_functions import logger

onlyTest = False
viaBackend = False
configs = ""
testsCatalog = ""
masterIP = ""
totalCost = 0
start = time.time()


def initAndChecks():
    """Initial checks and initialization of variables."""

    global configs
    global testsCatalog
    global masterIP
    with open("../configs.yaml", 'r') as stream:
        try:
            configs = yaml.load(stream, Loader=yaml.FullLoader)
        except yaml.YAMLError as exc:
            print(exc)
            sys.exit(1)
        except AttributeError:
            configs = yaml.load(stream)
    with open("../testsCatalog.yaml", 'r') as stream:
        try:
            testsCatalog = yaml.load(stream, Loader=yaml.FullLoader)
        except yaml.YAMLError as exc:
            print(exc)
            sys.exit(1)
        except AttributeError:
            testsCatalog = yaml.load(stream)
    if viaBackend is not True:
        try:
            with open('../master_ip', 'r') as infile:
                masterIP = infile.read()
            masterIP = masterIP.strip()
        except IOError:
            logger("master_ip file not found! Was infrastructure already created?", "!")
            sys.exit(2)
        infraStatus = subprocess.call(
            "ping -c 6 %s " % (masterIP), shell=True, stdout=open(os.devnull, 'w'), stderr=subprocess.STDOUT)
        if infraStatus != 0:
            logger("Infrastructure not reachable! Was it already created?", "!")
            sys.exit(2)


def fetchResults(source, file):
    """Fetch tests results file from pod.

    Parameters:
        source (str): Location of the results file.
        file (str): Name to be given to the file.

    """

    while os.path.exists(resDir + "/" + file) is False:
        kubectl("cp", source, resDir + "/" + file, None, True)
    print(file + " fetched!")


def kubectl(action, arg1, arg2, more, hideLogs):
    """Run kubectl commands. (use None for unset vars)

    Parameters:
        action (str): Action to be carried out. One of: create, delete or cp.
        arg1 (str): (Depends on 'action').
        arg2 (str): (Depends on 'action').
        more (str): Command to be appended to the kubectl command.
        hideLogs (bool): True hides logs, False doesn't.

    Return:
        integer: kubectl output status code

    """

    if action == "create":
        cmd = "kubectl create -f %s" % (arg1)  # path
    elif action == "delete":
        cmd = "kubectl delete %s %s" % (arg1, arg2)  # resourceType,name
    elif action == "cp":
        cmd = "kubectl cp %s %s" % (arg1, arg2)  # source,target
    if more is not None:
        cmd = cmd + more
    if hideLogs is True:
        return subprocess.call(cmd, shell=True, stdout=open(os.devnull, 'w'), stderr=subprocess.STDOUT)
    return os.system(cmd)

def writeFail(file, str):
    """Writes results file in case of errors.

    Parameters:
        file (str): Name of the results file.
        str (str): Message to write to results file.
    """

    print(str)
    with open(resDir + "/" + file, 'w') as outfile:
        json.dump({"info": str, "result": "fail"},
                  outfile, indent=4, sort_keys=True)


def correctlySet(value):
    return value >= 0 and isinstance(value, (int, float))


def checkRequiredCosts():
    """Check whether required costs are correctly set.

    Returns:
        bool: True in case required costs are correctly set. False otherwise.
    """
    res = True
    # No calculation at all in case of missing VM price
    if correctlySet(configs["costCalculation"]["instancePrice"]):
        if testsCatalog["s3Test"]["run"] is True:
            res = correctlySet(configs["costCalculation"]["s3bucketPrice"])
        # check here for other tests requiring additional resources besides VMs
    else:
        res = False
    if not res:
        print("Required costs aren't correctly set: calculation will not be made.")
    return res


def s3Test():
    """Run S3 endpoints test. """

    logger("S3 TEST", "=")

    with open('s3/raw/s3pod_raw.yaml', 'r') as infile:
        s3pod = infile.read().replace(
            "ENDPOINT_PH", testsCatalog["s3Test"]["endpoint"])
        s3pod = s3pod.replace("ACCESS_PH", testsCatalog["s3Test"]["accessKey"]).replace(
            "SECRET_PH", testsCatalog["s3Test"]["secretKey"])
    with open("s3/s3pod.yaml", 'w') as outfile:
        outfile.write(s3pod)
    start = time.time()  # create bucket
    if kubectl("create", "s3/s3pod.yaml", None, " && echo Waiting for s3 results file... ", False) != 0:
        writeFail("s3Test.json", "Error deploying s3pod.")
        return False, 0
    fetchResults("s3pod:/home/s3_test.json", "s3_test.json")
    end = time.time()  # bucket deletion
    # cleanup
    print("Cluster cleanup...")
    kubectl("delete", "pod", "s3pod", None, True)
    return True, float(configs["costCalculation"]["s3bucketPrice"]) * (end - start) / 3600

def dataRepatriationTest():
    """Run Data Repatriation Test -Exporting from cloud to Zenodo-"""

    logger("DATA REPATRIATION TEST", "=")

    with open('data_repatriation/raw/repatriation_pod_raw.yaml', 'r') as infile:
        with open("data_repatriation/repatriation_pod.yaml", 'w') as outfile:
            outfile.write(infile.read().replace(
                "PROVIDER_PH", configs["general"]["providerName"]))

    if kubectl("create", "data_repatriation/repatriation_pod.yaml", None, " && echo Waiting for Data Repatriation results file... ", False) != 0:
        writeFail("data_repatriation_test.json",
                  "Error deploying data_repatriation pod.")
        return False, 0
    fetchResults("repatriation-pod:/home/data_repatriation_test.json",
                 "data_repatriation_test.json")

    # cleanup
    print("Cluster cleanup...")
    kubectl("delete", "pod", "repatriation-pod", None, True)
    return True, 0

def perfsonarTest():
    """Run Networking Performance test -perfSONAR toolkit- """

    logger("PERFSONAR TEST", "=")

    with open('perfsonar/raw/ps_pod_raw.yaml', 'r') as infile:
        with open("perfsonar/ps_pod.yaml", 'w') as outfile:
            outfile.write(infile.read().replace(
                "ENDPOINT_PH", testsCatalog["perfsonarTest"]["endpoint"]))

    if kubectl("create", "perfsonar/ps_pod.yaml", None, " && echo Waiting for perfSONAR test results file... ", False) != 0:
        writeFail("perfsonar_results.json", "Error deploying perfsonar pod.")
        return False, 0
    while kubectl("cp", "perfsonar/ps_test.sh", "ps-pod:/tmp", None, True) != 0:
        pass  # Copy script to pod
    # Run copied script
    if os.system("kubectl exec -it ps-pod -- bash -c \"bash /tmp/ps_test.sh\"") != 0:
        writeFail("perfsonar_results.json",
                  "Error running script test on pod.")
        return False, 0
    fetchResults("ps-pod:/tmp/perfsonar_results.json",
                 "perfsonar_results.json")

    # cleanup
    print("Cluster cleanup...")
    kubectl("delete", "pod", "ps-pod", None, True)
    return True, 0


# ----------------CMD OPTIONS---------------------------------------------------
try:
    opts, args = getopt.getopt(sys.argv, "", ["--only-test", "--via-backend"])
except getopt.GetoptError as err:
    print(err)
    sys.exit(2)
for arg in args[1:len(args)]:
    if arg == '--only-test':
        onlyTest = True
    elif arg == '--via-backend':
        viaBackend = True
    else:
        print("Unknown option " + arg)

# ----------------CHECKS AND PREPARATION----------------------------------------
initAndChecks()

# ----------------CREATE RESULTS FOLDER AND GENERAL FILE------------------------
s3ResDirBase = configs["general"]["providerName"] + "/" + \
    str(datetime.datetime.now().strftime("%d-%m-%Y_%H-%M-%S"))
resDir = "../results/" + s3ResDirBase + "/detailed"
os.makedirs(resDir)
generalResults = {
    "infrastructure": str(os.popen("kubectl get nodes -owide | awk '{print $6}'").read()).replace("INTERNAL-IP\n", "").replace("\n", " ").strip(),
    "testing": []
}

# ----------------RUN THE TESTS AND CALCULATE COSTS-----------------------------
totalCost = 0
doCalc = checkRequiredCosts()
for key, value in testsCatalog.items():
    if testsCatalog[key]["run"] is True:
        # This is where the test is run
        testPass, testCost = eval(key+"()")
        generalResults["testing"].append({"test": key, "deployed": testPass})
        # test specific added costs (specific resources like s3)
        totalCost += testCost
if doCalc:
    duration = (time.time() - start) / 3600  # in hours
    totalCost += float(configs["general"]["clusterNodes"]) * \
        float(configs["costCalculation"]["instancePrice"]) * duration
    generalResults["estimatedCost"] = totalCost

# ----------------MANAGE RESULTS------------------------------------------------
with open("../results/" + s3ResDirBase + "/general.json", 'w') as outfile:
    json.dump(generalResults, outfile, indent=4, sort_keys=True)
msg1 = "TESTING COMPLETED. Results at:"
# No pushing results in case of local run (only ts-backend has AWS creds for this)
if viaBackend is True:
    pushResults = os.system("aws s3 cp --endpoint-url=https://s3.cern.ch %s s3://ts-results/%s --recursive > /dev/null" %
                             ("../results/" + s3ResDirBase, s3ResDirBase))
    os.system("cp \"../results/" + s3ResDirBase + "/general.json\" .. ")
    if pushResults != 0:
        logger("S3 upload failed! Is 'awscli' installed and configured?", "!")
    else:
        logger([msg1, "S3 bucket"], "#")
else:
    logger([msg1, "results/" + s3ResDirBase], "#")
