
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
    parser.add_argument("-t", "--threads", default=1, type=int,
            help="Threads to use (default: 1)")
    args = parser.parse_args()
    return args

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
        index_family = barcodeDict[barcode]
        index_family['seq_left'].append(seqLeft)
        index_family['seq_right'].append(seqRight)
        index_family['qual_left'].append(qualLeft)
        index_family['qual_right'].append(qualRight)
        return 0
    return 1

def recordsToDict(outputprefix, inFastq1, inFastq2, idxBase, barcodeCutOff, constant):
    barcodeDict = defaultdict(lambda: defaultdict(list))
    read_num = 0
    discarded_sequence_count = 0
    constant_left_length = len(constant_left)
    constant_right_length = len(constant_right)
    hamming_left_threshold = float(1)/constant_left_length
    hamming_right_threshold = float(1)/constant_right_length
    usable_left_seq = idxBase + constant_left_length
    usable_right_seq = idxBase + constant_right_length
    with gzip.open(inFastq1,'rb') as fq1, gzip.open(inFastq2,'rb') as fq2:
        for read1,read2 in izip(FastqGeneralIterator(fq1),FastqGeneralIterator(fq2)):
            discarded_sequence_count += readClustering(read1,read2,barcodeDict, idxBase, barcodeCutOff,
                                constant_left, constant_right, constant_left_length, constant_right_length,
                                hamming_left_threshold, hamming_right_threshold, usable_left_seq, usable_right_seq)
            read_num += 1
            if read_num % 1000000 == 0:
                stderr.write('[%s] Parsed: %i sequence\n' %(programname,read_num))
    stderr.write('[%s] Extracted: %i barcode sequences, discarded %i sequences\n' %(programname,len(barcodeDict.keys()), discarded_sequence_count))
    return barcodeDict, read_num


def clustering(outputprefix, inFastq1, inFastq2, idxBase, minReadCount,
               barcodeCutOff, constant_left, constant_right):
    h5_file = outputprefix + '.h5'
    barcode_file = outputprefix + '.txt'
    barcodeDict, read_num = recordsToDict(outputprefix, inFastq1, inFastq2, idxBase, barcodeCutOff, constant)
    barcodeCount = map(lambda x: len(barcodeDict[x]['seq_left']), barcodeDict.keys())
    p = plotBCdistribution(barcodeCount, outputprefix)
    dictToh5File(barcodeDict, h5_file, barcode_file)
    barcodeDict.clear()
    output_cluster_count, read1File, read2File = writingAndClusteringReads(outputprefix, minReadCount, h5_file, threads, barcode_file)
    # all done!
    stderr.write('[%s] Finished writing error free reads\n' %programname)
    stderr.write('[%s] [Summary]                        \n' %programname)
    stderr.write('[%s] read1:                     %s\n' %(programname, read1File))
    stderr.write('[%s] read2:                     %s\n' %(programname, read2File))
    stderr.write('[%s] output clusters:           %i\n' %(programname, output_cluster_count))
    stderr.write('[%s] Percentage retained:       %.3f\n' %(programname, float(output_cluster_count)/read_num * 100))
    return 0

def main(args):
    """
    main function:
        controlling work flow
        1. generate read clusters by reading from fq1 and fq2
        2. obtain concensus sequence from read clusters
        3. writing concensus sequence to files
    """
    start = time.time()
    outputprefix = args.outputprefix
    inFastq1 = args.fastq1
    inFastq2 = args.fastq2
    idxBase = args.idxBase
    minReadCount = args.cutoff
    barcodeCutOff = args.barcodeCutOff
    constant_left = args.constant_left
    constant_right = args.constant_right
    threads = args.threads

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
    args = getOptions()
    main(args)
