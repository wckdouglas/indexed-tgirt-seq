#!/bin/env python

from Bio.SeqIO.QualityIO import FastqGeneralIterator
from scipy.misc import logsumexp
from sys import stderr
from multiprocessing import Pool, Manager, Process
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Must be before importing matplotlib.pyplot or pylab
import matplotlib.pyplot as plt
import seaborn as sns
import sys
import argparse
import glob
import gzip
import time
from os import path
import os

sns.set_style('white')
programname = path.basename(sys.argv[0]).split('.')[0]

#    ==================      Sequence class sotring left right record =============
class seqRecord:
    def __init__(self):
        self.seqListRight = np.array([],dtype='string')
        self.qualListRight = np.array([],dtype='string')
        self.seqListLeft = np.array([],dtype='string')
        self.qualListLeft = np.array([],dtype='string')

    def addRecord(self, seqRight, qualRight, seqLeft, qualLeft):
        self.seqListRight = np.append(self.seqListRight,seqRight)
        self.qualListRight = np.append(self.qualListRight,qualRight)
        self.seqListLeft = np.append(self.seqListLeft,seqLeft)
        self.qualListLeft = np.append(self.qualListLeft,qualLeft)

    def readCounts(self):
        assert len(self.seqListLeft) == len(self.seqListRight), 'Not equal pairs'
        return len(self.seqListRight)

    def readLengthRight(self):
        return np.array([len(seq) for seq in self.seqListRight],dtype='int64')

    def readLengthLeft(self):
        return np.array([len(seq) for seq in self.seqListLeft],dtype='int64')

#======================  starting functions =============================
def getOptions():
    '''reading input 
    '''
    descriptions = 'Clustering fastq reads to fasta reads with the first $IDXBASE bases as cDNA-synthesis barcode. ' +\
                'Concensus bases are called only when the fraction of reads that contain the concensus base exceed some threshold. '+ \
                'Quality scores are generated by the average score for the bases that matched concensus base. ' 
    parser = argparse.ArgumentParser(description=descriptions)
    parser.add_argument('-o', '--outputprefix', required=True,
        help='Paired end Fastq files with R1_001.fastq.gz as suffix for read1, and R2_001.fastq.gz as suffix for read2')
    parser.add_argument('-1', '--fastq1', required=True,
        help='Paired end Fastq file 1 with four line/record')
    parser.add_argument('-2', '--fastq2',required=True,
        help='Paired end Fastq file 2 with four line/record')
    parser.add_argument('-m', '--cutoff', type=int,default=4,
        help="minimum read count for each read cluster (default: 4)")
    parser.add_argument("-t", "--threads", type=int, default=1,
        help="number of threads to use (default: 1)")
    parser.add_argument("-x", "--idxBase", type=int, default=13,
        help="how many base in 5' end as index? (default: 13)")
    parser.add_argument('-q', '--barcodeCutOff', type=int, default=30,
        help="Average base calling quality for barcode sequence (default=30)")
    parser.add_argument('-f', '--voteCutOff', type=float, default=0.9,
            help="The threshold of fraction in a position for a concensus base to be called (default: 0.9) ")
    parser.add_argument('-v', '--printScore', action = 'store_true',
            help="Printing score for each base to stdout (default: False)")
    parser.add_argument("-n", "--retainN", action='store_true',
        help="Use N-containing sequence for concensus base vote and output sequences containing N (defulat: False)")
    args = parser.parse_args()
    outputprefix = args.outputprefix
    inFastq1 = args.fastq1
    inFastq2 = args.fastq2
    idxBase = args.idxBase
    threads = args.threads
    minReadCount = args.cutoff
    retainN = args.retainN
    printScore = args.printScore
    barcodeCutOff = args.barcodeCutOff
    voteCutOff = args.voteCutOff
    return outputprefix, inFastq1, inFastq2, idxBase, threads, minReadCount, retainN, barcodeCutOff,  voteCutOff, printScore

def calculateConcensusBase(arg):
    """Given a list of sequences, 
        a list of quality line and 
        a position, 
    return the maximum likelihood base at the given position,
        along with the mean quality of these concensus bases.
    """
    seqList, qualList, pos,  voteCutOff, printScore, lock = arg
    columnBases = np.array([],dtype='string')
    qualities = np.array([],dtype='int64')
    for seq, qual in zip(seqList, qualList):
        columnBases = np.append(columnBases,seq[pos])
        qualities = np.append(qualities,ord(qual[pos]))
    uniqueBases, baseCount = np.unique(columnBases, return_counts=True)
    maxCount = np.amax(baseCount)
    concensusBase = uniqueBases[baseCount == maxCount][0] if np.true_divide(maxCount,np.sum(baseCount)) > voteCutOff else 'N'
    # offset -33
    quality = np.mean(qualities[columnBases==concensusBase]) if concensusBase in columnBases else 33
    return concensusBase, quality

def concensusSeq(seqList, qualList, positions,  voteCutOff, printScore, lock):
    """given a list of sequences, a list of quality and sequence length. 
        assertion: all seq in seqlist should have same length (see function: selectSeqLength)
    return a consensus sequence and the mean quality line (see function: calculateConcensusBase)
    """
    concensusPosition = map(calculateConcensusBase,[(seqList, qualList, pos,  voteCutOff, printScore, lock) for pos in positions])
    bases, quals = zip(*concensusPosition)
    sequence = ''.join(list(bases))
    quality = ''.join([chr(int(q)) for q in list(quals)])
    return sequence, quality


def concensusPairs(reads,  voteCutOff, printScore, lock):
    """ given a pair of reads as defined as the class: seqRecord
    return concensus sequence and mean quality of the pairs, 
        as well as the number of reads that supports the concnesus pairs
    see function: concensusSeq, calculateConcensusBase
    """
    # get concensus left reads first
    sequenceLeft, qualityLeft = concensusSeq(reads.seqListLeft, reads.qualListLeft,  
                                            range(np.unique(reads.readLengthLeft())[0]),  
                                             voteCutOff, printScore, lock)
    assert len(sequenceLeft) == len(qualityLeft), 'Wrong concensus sequence and quality!'
    # get concensus right reads first
    sequenceRight, qualityRight = concensusSeq(reads.seqListRight, reads.qualListRight,  
                                                range(np.unique(reads.readLengthRight())[0]), 
                                                 voteCutOff, printScore, lock)
    assert len(sequenceRight) == len(qualityRight), 'Wrong concensus sequence and quality!'
    return sequenceLeft, qualityLeft, len(reads.seqListLeft), \
            sequenceRight, qualityRight, len(reads.seqListRight)

def selectSeqLength(readLengthArray):
    """
    Given a list of sequence length of a read cluster from either side of the pair,
    select the sequence length with highest vote
    """
    seqlength, count = np.unique(readLengthArray, return_counts=True)
    return seqlength[count==max(count)][0]

def errorFreeReads(readCluster, index, counter, lock, minReadCount, 
        retainN, voteCutOff, printScore, results):
    """
    main function for getting concensus sequences from read clusters.
    return  a pair of concensus reads with a 4-line fastq format
    see functions: 1. filterRead, 
                  2. concensusPairs,
                  3. calculateConcensusBase
    """
    #if readCluster.readCounts() > minReadCount:
    #    reads = filterRead(readCluster)
    # skip if not enough sequences to perform voting
    if readCluster is not None and readCluster.readCounts() > minReadCount:
        sequenceLeft, qualityLeft, supportedLeftReads, \
        sequenceRight, qualityRight, supportedRightReads = concensusPairs(readCluster, voteCutOff, printScore, lock)
        if (retainN == False and 'N' not in sequenceRight and 'N' not in sequenceLeft) or (retainN == True and set(sequenceLeft)!={'N'}):
            lock.acquire()
            counter.value += 1
            clusterCount = counter.value
            leftRecord = '@cluster_%i %s %i readCluster\n%s\n+\n%s\n' \
                %(counter.value, index, supportedLeftReads, sequenceLeft, qualityLeft)
            rightRecord = '@cluster_%i %s %i readCluster\n%s\n+\n%s\n' \
                %(counter.value, index, supportedRightReads, sequenceRight, qualityRight)
            if clusterCount % 100000 == 0:
                stderr.write('[%s] Processed %i read clusters.\n' %(programname,clusterCount))
            lock.release()
            results.append((leftRecord,rightRecord))

def readClustering(args):
    """
    generate read cluster with a dictionary object and seqRecord class.
    index of the dictionary is the barcode extracted from first /idxBases/ of read 1 
    """
    read1, read2, barcodeDict, idxBase, barcodeCutOff, retainedN = args
    idLeft, seqLeft, qualLeft = read1
    idRight, seqRight, qualRight = read2
    assert idLeft.split(' ')[0] == idRight.split(' ')[0], 'Wrongly splitted files!! %s\n%s' %(idRight, idLeft)
    barcode = seqLeft[:idxBase]
    barcodeQualmean = int(np.mean([ord(q) for q in qualLeft[:idxBase]]) - 33)
    if ('N' not in barcode and barcodeQualmean > barcodeCutOff ) and \
    not any(pattern in barcode for pattern in ['AAAA','CCCC','TTTT','GGGG']) and \
    ((retainedN==False and 'N' not in seqLeft and 'N' not in seqRight) or retainedN==True): #and seqLeft[idxBase:(idxBase+6)] == 'TTTTGA':
        seqLeft = seqLeft[idxBase:]
        barcodeDict.setdefault(barcode,seqRecord()) 
        barcodeDict[barcode].addRecord(seqRight, qualRight, seqLeft, qualLeft)
    return 0

def writeFile(outputprefix, leftReads, rightReads):
    """
    write fastq lines to gzip files
    """
    # output file name
    read1File = outputprefix + '_R1_001.fastq.gz'
    read2File = outputprefix + '_R2_001.fastq.gz'
    with gzip.open(read1File,'wb') as read1, gzip.open(read2File,'wb') as read2:
        for left, right in zip(leftReads,rightReads):
            assert left.split(' ')[0] == right.split(' ')[0], 'Wrong order pairs!!'
            read1.write(left)
            read2.write(right)
    return read1File, read2File

def plotBCdistribution(barcodeDict, outputprefix):
    #plotting inspection of barcode distribution
    barcodeCount = np.array([record.readCounts() for record in barcodeDict.values()],dtype='int64')
    hist, bins = np.histogram(barcodeCount[barcodeCount<50],50)
    centers = (bins[:-1] + bins[1:]) / 2
    width = 0.7 * (bins[1] - bins[0])
    figurename = '%s.png' %(outputprefix)
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.bar(centers,hist,align='center',width=width)
    ax.set_xlabel("Number of occurence")
    ax.set_ylabel("Count of tags")
    ax.set_yscale('log',nonposy='clip')
    ax.set_title(outputprefix.split('/')[-1])
    fig.savefig(figurename)
    stderr.write('Plotted %s.\n' %figurename)
    return 0

def clusteringAndJoinFiles(outputprefix, inFastq1, inFastq2, idxBase, threads, minReadCount,
         retainN, barcodeCutOff, voteCutOff, printScore):
    barcodeDict = {}
    with gzip.open(inFastq1,'rb') as fq1, gzip.open(inFastq2,'rb') as fq2:
        map(readClustering,[(read1,read2,barcodeDict, idxBase, barcodeCutOff, retainN) for read1,read2 in zip(FastqGeneralIterator(fq1),FastqGeneralIterator(fq2))])
    stderr.write('[%s] Extracted: %i barcodes sequence\n' %(programname,len(barcodeDict.keys())))
    plotBCdistribution(barcodeDict, outputprefix)

    # From index library, generate error free reads
    # using multicore to process read clusters
    counter = Manager().Value('i',0)
    lock = Manager().Lock()
    results = Manager().list([])
    pool = Pool(processes=threads)
    [pool.apply_async(errorFreeReads, (barcodeDict[index],index, counter, lock, 
                        minReadCount, retainN,  voteCutOff, printScore, results)) \
                for index in barcodeDict.keys()]
    pool.close()
    pool.join()
    # since some cluster that do not have sufficient reads
    # will return None, results need to be filtered
    if (len(results) == 0):
        sys.exit('[%s] No concensus clusters!! \n' %(programname))
    left, right = zip(*results)
    stderr.write('[%s] Extracted error free reads\n' %(programname))
    # use two cores for parallel writing file
    read1File, read2File = writeFile(outputprefix, list(left), list(right))

    # all done!
    stderr.write('[%s] Finished writing error free reads\n' %programname)
    stderr.write('[%s]     read1:            %s\n' %(programname, read1File))
    stderr.write('[%s]     read2:            %s\n' %(programname, read2File))
    stderr.write('[%s]     output clusters:  %i\n' %(programname, len(left)))
    return 0

def main(outputprefix, inFastq1, inFastq2, idxBase, threads, minReadCount,
            retainN, barcodeCutOff, voteCutOff, printScore):
    """
    main function:
        controlling work flow
        1. generate read clusters by reading from fq1 and fq2
        2. obtain concensus sequence from read clusters
        3. writing concensus sequence to files
    """
    start = time.time()

    #print out parameters
    stderr.write( '[%s] Using parameters: \n')
    stderr.write( '[%s]     threads:                           %i\n' %(programname,threads))
    stderr.write( '[%s]     indexed bases:                     %i\n' %(programname,idxBase))
    stderr.write( '[%s]     minimum coverage:                  %i\n' %(programname,minReadCount))
    stderr.write( '[%s]     outputPrefix:                      %s\n' %(programname,outputprefix))
    stderr.write( '[%s]     retaining N-containing sequence:   %r\n' %(programname,retainN))
    stderr.write( '[%s]     base fraction cutoff:              %.3f\n' %(programname,voteCutOff))
    
    # divide reads into subclusters
    tempDir = '%s_tmp' %outputprefix
    os.system('rm -rf  %s' %tempDir)
    stderr.write('[%s] Cleaned %s\n'%(programname,tempDir))
    os.system('mkdir -p %s' %tempDir)
    subIdx = 0
    if subIdx > 1:
        subFq1, subFq2 = subCluster(inFastq1, inFastq2, subIdx, outputprefix, threads)
        for subFq1, subFq2 in zip(subFq1, subFq2):
            clusteringAndJoinFiles(outputprefix, subFq1, subFq2, idxBase, threads, minReadCount,
                                retainN, barcodeCutOff, voteCutOff, printScore)
    else:
        clusteringAndJoinFiles(outputprefix, inFastq1, inFastq2, idxBase, threads, minReadCount,
                                retainN, barcodeCutOff, voteCutOff, printScore)
    os.system('rm -rf  %s' %tempDir)
    stderr.write('[%s] Cleaned %s\n'%(programname,tempDir))
    stderr.write('[%s]     time lapsed:      %2.3f min\n' %(programname, np.true_divide(time.time()-start,60)))
    return 0
        
if __name__ == '__main__':
    outputprefix, inFastq1, inFastq2, idxBase, threads, minReadCount, \
            retainN, barcodeCutOff, voteCutOff, printScore = getOptions()
    main(outputprefix, inFastq1, inFastq2, idxBase, threads, minReadCount, 
            retainN, barcodeCutOff, voteCutOff, printScore)
