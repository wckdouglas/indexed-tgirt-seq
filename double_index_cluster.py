
#!/bin/env python

from Bio.SeqIO.QualityIO import FastqGeneralIterator
from sys import stderr
import numpy as np
import sys
import argparse
import glob
import gzip
import time
import os
from itertools import izip, imap
from cluster_reads import *
from collections import defaultdict
programname = os.path.basename(sys.argv[0]).split('.')[0]

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
    parser.add_argument("-x", "--idxBase", type=int, default=13,
        help="how many base in 5' end as index? (default: 13)")
    parser.add_argument('-q', '--barcodeCutOff', type=int, default=30,
        help="Average base calling quality for barcode sequence (default=30)")
    parser.add_argument("-l", "--constant_left", default='',
            help="Constant sequence after tags (default: '')")
    parser.add_argument("-r", "--constant_right", default='',
            help="Constant sequence after tags (default: '')")
    args = parser.parse_args()
    outputprefix = args.outputprefix
    inFastq1 = args.fastq1
    inFastq2 = args.fastq2
    idxBase = args.idxBase
    minReadCount = args.cutoff
    barcodeCutOff = args.barcodeCutOff
    constant_left = args.constant_left
    constant_right = args.constant_right
    return outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, constant_left, constant_right

def concensusPairs(reads):
    """ given a pair of reads as defined as the class: seqRecord
    return concensus sequence and mean quality of the pairs,
        as well as the number of reads that supports the concnesus pairs
    see function: concensusSeq, calculateConcensusBase
    """
    # get concensus left reads first
    sequenceLeft, qualityLeft = concensusSeq(reads.seqListLeft, reads.qualListLeft, range(np.unique(reads.readLengthLeft())[0]))
    assert len(sequenceLeft) == len(qualityLeft), 'Wrong concensus sequence and quality!'
    # get concensus right reads first
    sequenceRight, qualityRight = concensusSeq(reads.seqListRight, reads.qualListRight, range(np.unique(reads.readLengthRight())[0]))
    assert len(sequenceRight) == len(qualityRight), 'Wrong concensus sequence and quality!'
    return sequenceLeft, qualityLeft, sequenceRight, qualityRight

def errorFreeReads(args):
    """
    main function for getting concensus sequences from read clusters.
    return  a pair of concensus reads with a 4-line fastq format
    see functions: 1. filterRead,
                  2. concensusPairs,
                  3. calculateConcensusBase
    """
    # skip if not enough sequences to perform voting
    readCluster, index, minReadCount = args
    if readCluster is not None and readCluster.member_count > minReadCount:
        sequenceLeft, qualityLeft, sequenceRight, qualityRight = concensusPairs(readCluster)
        leftRecord = '%s_%i_readCluster\n%s\n+\n%s\n' \
            %(index, readCluster.member_count, sequenceLeft, qualityLeft)
        rightRecord = '%s_%i_readCluster\n%s\n+\n%s\n' \
            %(index, readCluster.member_count, sequenceRight, qualityRight)
    return leftRecord, rightRecord


def readClustering(read1,read2,barcodeDict, idxBase, barcodeCutOff,
                    constant_left, constant_right, constant_left_length, constant_right_length,
                    hamming_left_threshold, hamming_right_threshold, usable_left_seq, usable_right_seq):
    """
    generate read cluster with a dictionary object and seqRecord class.
    index of the dictionary is the barcode extracted from first /idxBases/ of read 1
    """
    idLeft, seqLeft, qualLeft = read1
    idRight, seqRight, qualRight = read2
    assert idLeft.split(' ')[0] == idRight.split(' ')[0], 'Wrongly splitted files!! %s\n%s' %(idRight, idLeft)
    barcode_left = seqLeft[:idxBase]
    barcode_right = seqRight[:idxBase]
    constant_left_region = seqLeft[idxBase:usable_left_seq]
    constant_right_region = seqRight[idxBase:usable_right_seq]
    barcode_qual_mean_left = int(np.mean(map(ord,qualLeft[:idxBase])) - 33)
    barcode_qual_mean_right = int(np.mean(map(ord,qualRight[:idxBase])) - 33)
    index = barcode_left + '/' + barcode_right
    if ('N' not in index \
            and np.min([barcode_qual_mean_right, barcode_qual_mean_left]) > barcodeCutOff \
            and not any(pattern in index for pattern in ['AAAAA','CCCCC','TTTTT','GGGGG']) \
            and hammingDistance(constant_right_region, constant_right) <= hamming_right_threshold \
            and hammingDistance(constant_left_region, constant_left) <= hamming_left_threshold):
        seqLeft = seqLeft[usable_left_seq:]
        qualLeft = qualLeft[usable_left_seq:]
        seqRight = seqRight[usable_right_seq:]
        qualRight = qualRight[usable_right_seq:]
        barcodeDict[index].addRecord(seqRight, qualRight, seqLeft, qualLeft)
        return 1
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

def clustering(outputprefix, inFastq1, inFastq2, idxBase, minReadCount,
               barcodeCutOff, constant_left, constant_right):
    barcodeDict = defaultdict(seqRecord)
    read_num = 0
    constant_left_length = len(constant_left)
    constant_right_length = len(constant_right)
    hamming_left_threshold = float(1)/constant_left_length
    hamming_right_threshold = float(1)/constant_right_length
    usable_left_seq = idxBase + constant_left_length
    usable_right_seq = idxBase + constant_right_length
    with gzip.open(inFastq1,'rb') as fq1, gzip.open(inFastq2,'rb') as fq2:
        for read1,read2 in izip(FastqGeneralIterator(fq1),FastqGeneralIterator(fq2)):
            readClustering(read1,read2,barcodeDict, idxBase, barcodeCutOff,
                    constant_left, constant_right, constant_left_length, constant_right_length,
                    hamming_left_threshold, hamming_right_threshold, usable_left_seq, usable_right_seq)
            read_num += 1
            if read_num % 1000000 == 0:
                stderr.write('[%s] Parsed: %i sequence\n' %(programname,read_num))
    stderr.write('[%s] Extracted: %i barcodes sequence\n' %(programname,len(barcodeDict.keys())))
    barcodeCount = map(lambda x: barcodeDict[x].member_count, barcodeDict.keys())
    p = plotBCdistribution(barcodeCount, outputprefix)

    # From index library, generate error free reads
    # using multicore to process read clusters
    counter = 0
    output_cluster_count = 0
    read1File = outputprefix + '_R1_001.fastq.gz'
    read2File = outputprefix + '_R2_001.fastq.gz'
    pool = Pool(threads)
    dict_iter = barcodeDict.iteritems()
    args = ((seq_record, index, minReadCount) for index, seq_record in dict_iter)
    processes = pool.imap_unordered(errorFreeReads, args)
    with gzip.open(read1File,'wb') as read1, gzip.open(read2File,'wb') as read2:
        for p in processes:
            counter += 1
            if counter % 100000 == 0:
                stderr.write('[%s] Processed %i read clusters.\n' %(programname, counter))
            if p != None:
                leftRecord, rightRecord = p
                read1.write('@cluster%i_%s' %(output_cluster_count, leftRecord))
                read2.write('@cluster%i_%s' %(output_cluster_count, rightRecord))
                output_cluster_count += 1
    pool.close()
    pool.join()
    # all done!

    stderr.write('[%s] Finished writing error free reads\n' %programname)
    stderr.write('[%s] [Summary]                        \n' %programname)
    stderr.write('[%s] read1:                     %s\n' %(programname, read1File))
    stderr.write('[%s] read2:                     %s\n' %(programname, read2File))
    stderr.write('[%s] output clusters:           %i\n' %(programname, output_cluster_count))
    stderr.write('[%s] Percentage retained:       %.3f\n' %(programname, float(counter)/read_num * 100))
    return 0

def main(outputprefix, inFastq1, inFastq2, idxBase, minReadCount,
        barcodeCutOff, constant_left, constant_right):
    """
    main function:
        controlling work flow
        1. generate read clusters by reading from fq1 and fq2
        2. obtain concensus sequence from read clusters
        3. writing concensus sequence to files
    """
    start = time.time()

    #print out parameters
    stderr.write( '[%s] Using parameters: \n' %(programname))
    stderr.write( '[%s]     indexed bases:                     %i\n' %(programname,idxBase))
    stderr.write( '[%s]     minimum coverage:                  %i\n' %(programname,minReadCount))
    stderr.write( '[%s]     outputPrefix:                      %s\n' %(programname,outputprefix))
    stderr.write( '[%s]     using constant regions left:   %s\n' %(programname,constant_left))
    stderr.write( '[%s]     using constant regions right:   %s\n' %(programname,constant_right))

    # divide reads into subclusters
    clustering(outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, constant_left, constant_right)
    stderr.write('[%s]     time lapsed:      %2.3f min\n' %(programname, np.true_divide(time.time()-start,60)))
    return 0

if __name__ == '__main__':
    outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, constant_left, constant_right = getOptions()
    main(outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, constant_left, constant_right)
