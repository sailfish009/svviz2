import collections
import itertools
import logging
import numpy
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

try:
    from rpy2 import robjects as ro
    from rpy2.robjects import numpy2ri
    numpy2ri.activate()
except ImportError:
    ro = None

class YassException(Exception):
    pass


def can_generate_dotplots():
    if ro is None:
        logger.warn("rpy2 could not be imported; dotplots will not be generated")
        return False
    try:
        subprocess.check_call("yass --version", stderr=subprocess.PIPE, shell=True)
    except subprocess.CalledProcessError:
        logger.warn("yass helper program could not be run; dotplots will not be generated")
        return False

    return True

_CAN_GENERATE_DOTPLOTS = can_generate_dotplots()

nucs = list("ACGT")
dinucs = ["".join(x) for x in itertools.product(nucs, repeat=2) if len(set(x))!=1]
trinucs = ["".join(x) for x in itertools.product(nucs, repeat=3) if len(set(x))!=1]

def detect_simple_repeats(seq):
    patterns = [["({}){{10,}}".format(nuc) for nuc in nucs],
                ["({}){{5,}}".format(dinuc) for dinuc in dinucs],
                ["({}){{3,}}".format(trinuc) for trinuc in trinucs]]

    repeats = []

    for pattern in patterns:
        pattern = "|".join(pattern)
        for match in re.finditer(pattern, seq):
            repeats.append((match.start(), match.end()))

    return repeats

def plot_simple_repeats(s1, s2):
    repeats1 = detect_simple_repeats(s1)
    repeats2 = detect_simple_repeats(s2)

    from_axis = max([len(s1), len(s2)]) * 0.01

    for repeat in repeats1:
        ro.r.segments(repeat[0], from_axis, repeat[1], from_axis, lwd=5, lend=1, col="red")
    for repeat in repeats2:
        ro.r.segments(from_axis, repeat[0], from_axis, repeat[1], lwd=5, lend=1, col="red")


def generate_dotplots(datahub):
    if not _CAN_GENERATE_DOTPLOTS:
        return

    parts = collections.OrderedDict()

    for allele in ["alt", "ref"]:
        for part in datahub.variant.chrom_parts(allele):
            parts[part.id] = part
            print(part.id, part.get_seq())

    outpath = os.path.join(
        datahub.args.outdir, "{}.dotplots.pdf".format(datahub.variant.short_name()))
    ro.r.pdf(outpath)

    print(parts.keys())
    for i, part1 in enumerate(parts.keys()):
        for part2 in list(parts.keys())[i:]:
            draw_dotplot(parts[part1], parts[part2])

    ro.r["dev.off"]()



def draw_dotplot(part1, part2):
    breakpoints1 = numpy.cumsum([len(segment) for segment in part1.segments])[:-1]
    breakpoints2 = numpy.cumsum([len(segment) for segment in part2.segments])[:-1]

    print("::", part1.id, part2.id)

    yass_dotplot(part1.get_seq(), part2.get_seq(),
                 breakpoints1, breakpoints2,
                 part1.id, part2.id)

    plot_simple_repeats(part1.get_seq(), part2.get_seq())

    # dotplot2(part1.get_seq(), part2.get_seq())


def yass_dotplot(s1, s2, breakpoints1, breakpoints2, label1, label2):
    tempDir = tempfile.mkdtemp()
    
    outpaths = []
    
    for i, seq in enumerate([s1,s2]):
        tempFastaPath = os.path.join(tempDir, "seq{}.fa".format(i))
        outpaths.append(tempFastaPath)
        with open(tempFastaPath, "w") as tempFastaFile:
            tempFastaFile.write(">seq\n{}".format(seq))
            tempFastaFile.close()

    tempYASSResult = os.path.join(tempDir, "result.txt")
    
    gapExtend = -int(max([len(s1), len(s2)]) / 2      # half the length the entire sequence
                                             / 10     # 10 nt insertion
                                             * 5      # the match bonus
                     )
    print(gapExtend)
    
    # yassCommand = "yass -d 1 -G -50,{} -E 10 {} {}".format(gapExtend, outpaths[0], outpaths[1])
    # subprocess.check_call(yassCommand, shell=True)

    yassCommand = "yass -d 3 -G -50,{} -E 10 -o {} {} {}".format(gapExtend, tempYASSResult, outpaths[0], outpaths[1])
    proc = subprocess.Popen(yassCommand, shell=True,
        stderr=subprocess.PIPE)
    resultCode = proc.wait()
    
    if resultCode != 0:
        raise YassException("Check that yass is installed correctly")
    stderr = proc.stderr.readlines()[0].decode()
    if "Error" in stderr:
        print("Error running yass: '{}'".format(stderr))
        raise YassException("Error running yass")

    ro.r.plot(ro.IntVector([0]), ro.IntVector([0]), type="n", 
              main="{} : {}".format(label1, label2),
              xaxs="i", yaxs="i", 
              xlab="Position in {}".format(label1),
              ylab="Position in {}".format(label2),
              xlim=ro.IntVector([0,len(s1)]),
              ylim=ro.IntVector([0,len(s2)]))
    
    # for breakpoint in breakpoints1:
    ro.r.abline(v=breakpoints1, lty=2, col="gray")
        
    # for breakpoint in breakpoints2:
    ro.r.abline(h=breakpoints2, lty=2, col="gray")
        
    for line in open(tempYASSResult):
        if line.startswith("#"):continue
            
        res = line.strip().split()
        if res[6]=="f":
            ro.r.segments(int(res[0]), int(res[2]), int(res[1]), int(res[3]), col="blue", lwd=1)
        else:
            ro.r.segments(int(res[1]), int(res[2]), int(res[0]), int(res[3]), col="red", lwd=1)




def dotplot2(s1, s2, wordsize=6, overlap=3, verbose=10):
    """ verbose = 0 (no progress), 1 (progress if s1 and s2 are long) or
    2 (progress in any case) """
    print("START")

    doProgress = False
    if verbose > 1 or len(s1)*len(s2) > 1e6:
        doProgress = True
    
    l1 = int((len(s1)-wordsize)/overlap+2)
    l2 = int((len(s2)-wordsize)/overlap+2)

    mat = numpy.ones((l1, l2), dtype="int8")
    mat[:] = 1

    for i in range(0, len(s1)-wordsize, overlap):
        if i % 1000 == 0 and doProgress:
            logger.info("  dotplot progress: {} of {} rows done".format(i, len(s1)-wordsize))
        word1 = s1[i:i+wordsize]

        for j in range(0, len(s2)-wordsize, overlap):
            word2 = s2[j:j+wordsize]

            if word1 == word2 or word1 == word2[::-1]:
                mat[int(i/overlap), int(j/overlap)] = 0

    x1 = 0
    y1 = 0
    x2 = mat.shape[0]
    y2 = mat.shape[1]

    ro.r.plot(numpy.array([0]),
           xlim=numpy.array([x1,x2]),
           ylim=numpy.array([y1,y2]),
           type="n", bty="n",
           main="", xlab="", ylab="")

    rasterized = ro.r["as.raster"](mat, max=mat.max())
    ro.r.rasterImage(rasterized, x1, y1, x2, y2)
    print("/PLOT")
    # imgData = None
    # tempDir = tempfile.mkdtemp()
    # try:
    #     path = os.path.join(tempDir, "dotplot.png")
    #     misc.imsave(path, mat)
    #     imgData = open(path, "rb").read()
    # except Exception as e:
    #     logging.error("Error generating dotplots:'{}'".format(e))
    # finally:
    #     shutil.rmtree(tempDir)
    # return Image(imgData)