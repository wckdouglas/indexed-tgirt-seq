#!/bin/env python

import matplotlib
matplotlib.use('Agg')
from Bio.SeqIO.QualityIO import FastqGeneralIterator
from sys import stderr
from scipy.spatial.distance import hamming
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import sys
import argparse
import glob
import gzip
import time
import os
from itertools import izip, product
from multiprocessing import Pool, Manager
import h5py 
sns.set_style('white')
programname = os.path.basename(sys.argv[0]).split('.')[0]
minQ = 33
maxQ = 73

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
    parser.add_argument("-x", "--idxBase", type=int, default=13,
        help="how many base in 5' end as index? (default: 13)")
    parser.add_argument('-q', '--barcodeCutOff', type=int, default=30,
        help="Average base calling quality for barcode sequence (default=30)")
    parser.add_argument("-t", "--threads", type=int, default = 1,
        help="Threads to use (default: 1)")
    parser.add_argument("-c", "--constant_region", default='',
            help="Constant sequence after tags (default: '')")
    args = parser.parse_args()
    outputprefix = args.outputprefix
    inFastq1 = args.fastq1
    inFastq2 = args.fastq2
    idxBase = args.idxBase
    minReadCount = args.cutoff
    barcodeCutOff = args.barcodeCutOff
    threads = args.threads
    constant = args.constant_region
    return outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, threads, constant

def hammingDistance(expected_constant, constant_region):
    dist = hamming(list(expected_constant),list(constant_region))
    return dist

def qual2Prob(q):
    ''' 
    Given a q list,
    return a list of prob
    '''
    return np.power(10, np.true_divide(-q,10))

def calculatePosterior(guessBase, columnBases, qualities):
    qualHit = qualities[columnBases==guessBase]
    qualMissed = qualities[columnBases!=guessBase]
    if len(qualMissed) > 0:
        hit = np.prod(1- qual2Prob(qualHit)) if len(qualHit) > 0 else 0
        missed = np.prod(np.true_divide(qual2Prob(qualMissed),3))
        posterior = missed * hit
    else: 
        posterior = 1
    return posterior

def calculateConcensusBase(arg):
    """Given a list of sequences, 
        a list of quality line and 
        a position, 
    return the maximum likelihood base at the given position,
        along with the mean quality of these concensus bases.
    """
    seqList, qualList, pos = arg
    no_of_reads = len(seqList)
    acceptable_bases = np.array(['A','C','T','G'], dtype='string')
    columnBases = np.zeros(no_of_reads,dtype='string')
    qualities = np.zeros(no_of_reads,dtype=np.int64)
    for seq, qual, i  in zip(seqList, qualList, range(no_of_reads)):
        columnBases[i] = seq[pos]
        qualities[i] = ord(qual[pos]) - 33
    posteriors = [calculatePosterior(guessBase, columnBases, qualities) for guessBase in acceptable_bases]
    posteriors = np.true_divide(posteriors, np.sum(posteriors))
    maxLikHood = np.argmax(posteriors)
    concensusBase = acceptable_bases[maxLikHood]
    posterior = posteriors[maxLikHood]
    quality = -10 * np.log10(1 - posterior) + 33 if posterior < 1 else maxQ
    return concensusBase, quality

def concensusSeq(seqList, qualList, positions):
    """given a list of sequences, a list of quality and sequence length. 
        assertion: all seq in seqlist should have same length (see function: selectSeqLength)
    return a consensus sequence and the mean quality line (see function: calculateConcensusBase)
    """
    concensusPosition = map(calculateConcensusBase,[(seqList, qualList, pos) for pos in positions])
    bases, quals = zip(*concensusPosition)
    quality = np.array(quals,dtype=np.int64)
    quality[quality<minQ] = minQ
    quality[quality > maxQ] = maxQ
    sequence = ''.join(list(bases))
    quality = ''.join(map(chr,quality))
    return sequence, quality


def concensusPairs(read_cluster_table):
    """ given a pair of reads as defined as the class: seqRecord
    return concensus sequence and mean quality of the pairs, 
        as well as the number of reads that supports the concnesus pairs
    see function: concensusSeq, calculateConcensusBase
    """
    # get concensus left reads first
    read_cluster_table = read_cluster_table[read_cluster_table[:,0]!='']
    filtered_variables = map(lambda i: read_cluster_table[:,i], range(4))
    seqListLeft, seqListRight, qualListLeft, qualListRight = filtered_variables
    sequenceLeft, qualityLeft = concensusSeq(seqListLeft, qualListLeft, range(len(seqListLeft[0])))
    assert len(sequenceLeft) == len(qualityLeft), 'Wrong concensus sequence and quality!'
    # get concensus right reads first
    sequenceRight, qualityRight = concensusSeq(seqListRight, qualListRight, range(len(seqListLeft[0])))
    assert len(sequenceRight) == len(qualityRight), 'Wrong concensus sequence and quality!'
    return sequenceLeft, qualityLeft, len(seqListLeft), sequenceRight, qualityRight, len(seqListRight)

def selectSeqLength(readLengthArray):
    """
    Given a list of sequence length of a read cluster from either side of the pair,
    select the sequence length with highest vote
    """
    seqlength, count = np.unique(readLengthArray, return_counts=True)
    return seqlength[count==max(count)][0]

def errorFreeReads(args):
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
    path, hdf_name, counter, minReadCount = args
    with h5py.File(hdf_name,'r') as h5file:
        read_cluster_table = h5file.get(path).value
	sequenceLeft, qualityLeft, supportedLeftReads, sequenceRight, qualityRight, supportedRightReads = concensusPairs(read_cluster_table)
    index = path.split('/')[-1]
    counter.value += 1
    count = counter.value
    leftRecord = '@cluster_%i_%s %i readCluster\n%s\n+\n%s\n' \
	%(count, index, supportedLeftReads, sequenceLeft, qualityLeft)
    rightRecord = '@cluster_%i_%s %i readCluster\n%s\n+\n%s\n' \
        %(count, index, supportedRightReads, sequenceRight, qualityRight)
    if count % 100000 == 0:
        stderr.write('[%s] Processed %i read clusters.\n' %(programname, count))
    return (leftRecord,rightRecord)


def plotBCdistribution(barcodeCount, outputprefix):
    #plotting inspection of barcode distribution
    barcodeCount = np.array(barcodeCount, dtype=np.int64)
    num, count = np.unique(barcodeCount,return_counts=True)
    figurename = '%s.png' %(outputprefix)
    with sns.plotting_context('paper',font_scale=1.3):
        p = sns.barplot(num,count, color='salmon')
    p.set_xlabel("Number of occurence")
    p.set_ylabel("Count of tags")
    p.set_yscale('log',nonposy='clip')
    p.set_title(outputprefix.split('/')[-1])
    p.spines['right'].set_visible(False)
    p.spines['top'].set_visible(False)
    plt.savefig(figurename)
    stderr.write('Plotted %s.\n' %figurename)
    return 0

def open_temp_hdf(n,outputprefix):
    hdf_name = outputprefix  + '.h5'	
    with h5py.File(hdf_name,'w') as h5file:
	for splitCode in product('ACTG',repeat=n):
	    prefix = ''.join(splitCode)
	    h5file.create_group(prefix)
    return hdf_name


def readClustering(read1, read2, idxBase, barcodeCutOff, constant, h5file, n, barcode_count):
    """
    generate read cluster with a dictionary object and seqRecord class.
    index of the dictionary is the barcode extracted from first /idxBases/ of read 1 
    """
    idLeft, seqLeft, qualLeft = read1
    idRight, seqRight, qualRight = read2
    assert idLeft.split(' ')[0] == idRight.split(' ')[0], 'Wrongly splitted files!! %s\n%s' %(idRight, idLeft)
    barcode = seqLeft[:idxBase]
    qualLeft = map(ord,qualLeft)
    qualRight = map(ord,qualRight)
    constant_length = len(constant)
    constant_region = seqLeft[idxBase:idxBase+constant_length] if constant_length > 0 else 0
    barcodeQualmean = int(np.mean(qualLeft[:idxBase]) - 33)
    prefix = barcode[:n]	
    if ('N' not in barcode \
	    and barcodeQualmean > barcodeCutOff \
	    and not any(pattern in barcode for pattern in ['AAAAA','CCCCC','TTTTT','GGGGG']) \
	    and hammingDistance(constant, constant_region) < 0.3 \
	    and 'N' not in prefix):
        seqLeft = seqLeft[idxBase+constant_length:]
        qualLeft = qualLeft[idxBase+constant_length:]
	barcode_count.setdefault(barcode, 0)
	try:
	    table = h5file[prefix][barcode]
	except KeyError:
	    table = h5file[prefix].create_dataset(barcode, shape=(30,4),maxshape=(100,4), dtype='S256')
	i = barcode_count[barcode]
	table[i,:] = np.array([seqLeft, seqRight,''.join(map(chr,qualLeft)),''.join(map(chr,qualRight))])
	barcode_count[barcode] += 1
    return barcode_count

def clustering(outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, 
	threads, constant, read1File, read2File):
    prefix_length = 4
    hdf_name = open_temp_hdf(prefix_length, outputprefix)
    barcode_count = {}
    barcodeCounts = []
    i = 0
    outClusterCount = 0
    with gzip.open(inFastq1,'rb') as fq1, gzip.open(inFastq2,'rb') as fq2, \
	    h5py.File(hdf_name,'a') as h5file:
	for read1,read2 in izip(FastqGeneralIterator(fq1),FastqGeneralIterator(fq2)):
	    i += 1
	    barcode_count = readClustering(read1,read2, idxBase, barcodeCutOff,  
		    constant, h5file, prefix_length, barcode_count) 
	    if i % 100000 == 0:
		sys.stderr.write('Parsed: %i read sequence\n' %i)
	
    with h5py.File(hdf_name,'r') as h5file:
	counter = Manager().Value('i',0)
        barcode_family_count = 0
	for prefix in h5file.iterkeys():
	    group = h5file.get(prefix)
	    paths = []
	    for index in group.iterkeys():
		table = group[index].value
		family_size = table[table[:,0]!=''].shape[0]
		barcodeCounts.append(family_size)
		barcode_family_count += 1
		if family_size > minReadCount:
		    path = prefix + '/' + index
		    paths.append(path)

	    # To reduce memory use, this fragments is moved in to every prefix
	    # From index library, generate error free reads
	    # using multicore to process read clusters
	    args = [(path, hdf_name, counter, minReadCount) for path in paths]
	    pool = Pool(threads)
	    processes = pool.map_async(errorFreeReads, args)
	    results = processes.get()
	    pool.close()
	    pool.join()
	    # since some cluster that do not have sufficient reads
	    # will return None, results need to be filtered
	    if len(results) > 0:
		left, right = zip(*results)
		with gzip.open(read1File,'wb') as read1, gzip.open(read2File,'wb') as read2:
		    outClusterCount = writeFile(read1, read2, left, right, outClusterCount)
    stderr.write('[%s] Extracted: %i barcodes sequence\n' %(programname,barcode_family_count))

    return barcodeCounts,outClusterCount


def writeFile(read1, read2, leftReads, rightReads, outClusterCount):
    for left, right in zip(list(leftReads),list(rightReads)):
	if left != 0:
	    assert left.split(' ')[0] == right.split(' ')[0], 'Wrong order pairs!!'
	    read1.write(left)
	    read2.write(right)
	    outClusterCount += 1
    return outClusterCount

def main(outputprefix, inFastq1, inFastq2, idxBase, minReadCount,  barcodeCutOff, threads, constant):
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
    stderr.write( '[%s]     threads:                           %i\n' %(programname, threads))
    stderr.write( '[%s]     minimum coverage:                  %i\n' %(programname,minReadCount))
    stderr.write( '[%s]     outputPrefix:                      %s\n' %(programname,outputprefix))
    stderr.write( '[%s]     using constant regions:   %s\n' %(programname,constant))
    
    # divide reads into subclusters
    read1File = outputprefix + '_R1_001.fastq.gz'
    read2File = outputprefix + '_R2_001.fastq.gz'

    #start writing to new file 
    barcodeCount, outClusterCount = clustering(outputprefix, inFastq1, inFastq2, idxBase, 
				    minReadCount, barcodeCutOff, threads, constant,
				    read1File, read2File)
    # ending processes, and plot barcode count
    p = plotBCdistribution(barcodeCount[barcodeCount > 0], outputprefix)        
    stderr.write('[%s] Finished writing error free reads\n' %programname)
    stderr.write('[%s]     read1:            %s\n' %(programname, read1File))
    stderr.write('[%s]     read2:            %s\n' %(programname, read2File))
    stderr.write('[%s]     output clusters:  %i\n' %(programname, outClusterCount))
    stderr.write('[%s]     time lapsed:      %2.3f min\n' %(programname, np.true_divide(time.time()-start,60)))
    return 0
        
if __name__ == '__main__':
    outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, threads, constant = getOptions()
    main(outputprefix, inFastq1, inFastq2, idxBase, minReadCount, barcodeCutOff, threads, constant)
