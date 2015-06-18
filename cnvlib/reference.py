"""Supporting functions for the 'reference' command."""
from __future__ import absolute_import, division, print_function

import numpy as np
from Bio._py3k import map, zip

from . import core, metrics, ngfrills, params
from .cnary import CopyNumArray as CNA
from .ngfrills import echo


def bed2probes(bed_fname):
    """Create neutral-coverage probes from intervals."""
    cn_rows = [(chrom, start, end, name, 0, 0, 0, 0)
               for chrom, start, end, name in ngfrills.parse_regions(bed_fname)]
    return CNA.from_rows(cn_rows,
                         ('chromosome', 'start', 'end', 'gene', 'log2', 'gc',
                          'rmask', 'spread'),
                         {'sample_id': core.fbase(bed_fname)})


def combine_probes(filenames, fa_fname, is_male_reference):
    """Calculate the median coverage of each bin across multiple samples.

    Input:
        List of .cnn files, as generated by 'coverage' or 'import-picard'.
        `fa_fname`: fil columns for GC and RepeatMasker genomic values.
    Returns:
        A single CopyNumArray summarizing the coverages of the input samples,
        including each bin's "average" coverage, "spread" of coverages, and
        genomic GC content.
    """
    from cnvlib import fix  # XXX
    columns = {}

    # Load coverage from target/antitarget files
    echo("Loading", filenames[0])
    cnarr1 = CNA.read(filenames[0])
    if not len(cnarr1):
        # Just create an empty array with the right columns
        extra_cols = ['chromosome', 'start', 'end', 'gene', 'log2']
        if 'gc' in cnarr1 or fa_fname:
            extra_cols.append('gc')
        if fa_fname:
            extra_cols.append('rmask')
        extra_cols.append('spread')
        return CNA.from_rows([], extra_cols, {'sample_id': "reference"})

    # Calculate GC and RepeatMasker content for each probe's genomic region
    if fa_fname:
        gc, rmask = get_fasta_stats(cnarr1, fa_fname)
        columns['gc'] = gc
        columns['rmask'] = rmask
    elif 'gc' in cnarr1:
        # Reuse .cnn GC values if they're already stored (via import-picard)
        gc = cnarr1['gc']
        columns['gc'] = gc
    else:
        echo("No FASTA reference genome provided; skipping GC, RM calculations")

    # Make the sex-chromosome coverages of male and female samples compatible
    chr_x = cnarr1._chr_x_label
    chr_y = cnarr1._chr_y_label
    flat_coverage = cnarr1.expect_flat_cvg(is_male_reference)
    def shift_sex_chroms(cnarr):
        """Shift sample X and Y chromosomes to match the reference gender.

        Reference values:
            XY: chrX -1, chrY -1
            XX: chrX 0, chrY -1

        Plan:
          chrX:
            xx sample, xx ref: 0    (from 0)
            xx sample, xy ref: -= 1 (from -1)
            xy sample, xx ref: += 1 (from 0)    +1
            xy sample, xy ref: 0    (from -1)   +1
          chrY:
            xx sample, xx ref: = -1 (from -1)
            xx sample, xy ref: = -1 (from -1)
            xy sample, xx ref: 0    (from -1)   +1
            xy sample, xy ref: 0    (from -1)   +1

        """
        is_xx = cnarr.guess_xx()
        cnarr['log2'] += flat_coverage
        if is_xx:
            # chrX already OK
            # No chrY; it's all noise, so just match the male
            cnarr['log2'][cnarr.chromosome == chr_y] = -1.0
        else:
            # 1/2 #copies of each sex chromosome
            cnarr['log2'][(cnarr.chromosome == chr_x) |
                          (cnarr.chromosome == chr_y)] += 1.0

    edge_sorter = fix.make_edge_sorter(cnarr1, params.INSERT_SIZE)
    def bias_correct_coverage(cnarr):
        """Perform bias corrections on the sample."""
        cnarr.center_all()
        shift_sex_chroms(cnarr)
        if 'gc' in columns:
            echo("Correcting for GC bias...")
            cnarr = fix.center_by_window(cnarr, .1, columns['gc'])
        if 'rmask' in columns:
            echo("Correcting for RepeatMasker bias...")
            cnarr = fix.center_by_window(cnarr, .1, columns['rmask'])
        echo("Correcting for density bias...")
        cnarr = fix.center_by_window(cnarr, .1, edge_sorter)
        return cnarr['log2']

    # Pseudocount of 1 "flat" sample
    all_coverages = [flat_coverage, bias_correct_coverage(cnarr1)]
    for fname in filenames[1:]:
        echo("Loading target", fname)
        cnarrx = CNA.read(fname)
        # Bin information should match across all files
        if not (len(cnarr1) == len(cnarrx)
                and (cnarr1.chromosome == cnarrx.chromosome).all()
                and (cnarr1.start == cnarrx.start).all()
                and (cnarr1.end == cnarrx.end).all()
                and (cnarr1['gene'] == cnarrx['gene']).all()):
            raise RuntimeError("%s probes do not match those in %s"
                               % (fname, filenames[0]))
        all_coverages.append(bias_correct_coverage(cnarrx))
    all_coverages = np.vstack(all_coverages)

    echo("Calculating average bin coverages")
    cvg_centers = np.apply_along_axis(metrics.biweight_location, 0,
                                      all_coverages)
    echo("Calculating bin spreads")
    spreads = np.apply_along_axis(metrics.biweight_midvariance, 0,
                                  all_coverages)
    columns['spread'] = spreads
    columns.update({
        'chromosome': cnarr1.chromosome,
        'start': cnarr1.start,
        'end': cnarr1.end,
        'gene': cnarr1['gene'],
        'log2': cvg_centers,
    })
    return CNA.from_columns(columns, {'sample_id': "reference"})


def warn_bad_probes(probes):
    """Warn about target probes where coverage is poor.

    Prints a formatted table to stderr.
    """
    bad_probes = probes[mask_bad_probes(probes)]
    fg_index = (bad_probes['gene'] != 'Background')
    fg_bad_probes = bad_probes[fg_index]
    if len(fg_bad_probes) > 0:
        bad_pct = 100 * len(fg_bad_probes) / sum(probes['gene'] != 'Background')
        echo("*WARNING*", len(fg_bad_probes), "targets",
             "(%.4f)" % bad_pct + '%', "failed filters:")
        gene_cols = max(map(len, fg_bad_probes['gene']))
        labels = list(map(CNA.row2label, fg_bad_probes))
        chrom_cols = max(map(len, labels))
        last_gene = None
        for label, probe in zip(labels, fg_bad_probes):
            if probe['gene'] == last_gene:
                gene = '  "'
            else:
                gene = probe['gene']
                last_gene = gene
            if 'rmask' in probes:
                print("  %s  %s  coverage=%.3f  spread=%.3f  rmask=%.3f"
                      % (gene.ljust(gene_cols), label.ljust(chrom_cols),
                         probe['coverage'], probe['spread'], probe['rmask']))
            else:
                print("  %s  %s  coverage=%.3f  spread=%.3f"
                      % (gene.ljust(gene_cols), label.ljust(chrom_cols),
                         probe['log2'], probe['spread']))

    # Count the number of BG probes dropped, too (names are all "Background")
    bg_bad_probes = bad_probes[~fg_index]
    if len(bg_bad_probes) > 0:
        bad_pct = 100 * len(bg_bad_probes) / sum(probes['gene'] == 'Background')
        echo("Antitargets:", len(bg_bad_probes), "(%.4f)" % bad_pct + '%',
             "failed filters")


def mask_bad_probes(probes):
    """Flag the probes with excessively low or inconsistent coverage.

    Returns a bool array where True indicates probes that failed the checks.
    """
    mask = ((probes['log2'] < params.MIN_BIN_COVERAGE) |
            (probes['spread'] > params.MAX_BIN_SPREAD))
    if 'rmask' in probes:
        mask |= (probes['rmask'] > params.MAX_REPEAT_FRACTION)
    return np.asarray(mask)


def get_fasta_stats(probes, fa_fname):
    """Calculate GC and RepeatMasker content of each bin in the FASTA genome."""
    ngfrills.ensure_fasta_index(fa_fname)
    fa_coords = zip(probes.chromosome, probes.start, probes.end)
    echo("Calculating GC and RepeatMasker content in", fa_fname, "...")
    gc_rm_vals = [calculate_gc_lo(subseq)
                  for subseq in ngfrills.fasta_extract_regions(fa_fname,
                                                               fa_coords)]
    gc_vals, rm_vals = zip(*gc_rm_vals)
    return np.asfarray(gc_vals), np.asfarray(rm_vals)


def calculate_gc_lo(subseq):
    """Calculate the GC and lowercase (RepeatMasked) content of a string."""
    cnt_at_lo = subseq.count('a') + subseq.count('t')
    cnt_at_up = subseq.count('A') + subseq.count('T')
    cnt_gc_lo = subseq.count('g') + subseq.count('c')
    cnt_gc_up = subseq.count('G') + subseq.count('C')
    tot = float(cnt_gc_up + cnt_gc_lo + cnt_at_up + cnt_at_lo)
    if not tot:
        return 0.0, 0.0
    frac_gc = (cnt_gc_lo + cnt_gc_up) / tot
    frac_lo = (cnt_at_lo + cnt_gc_lo) / tot
    return frac_gc, frac_lo


def reference2regions(reference, coord_only=False):
    """Extract iterables of target and antitarget regions from a reference.

    Like loading two BED files with ngfrills.parse_regions.
    """
    cna2rows = (_cna2coords if coord_only else _cna2regions)
    return map(cna2rows, _ref_split_targets(reference))


def _cna2coords(cnarr):
    """Extract the coordinate columns from a CopyNumberArray"""
    return zip(cnarr['chromosome'], cnarr['start'], cnarr['end'])


def _cna2regions(cnarr):
    """Extract the region columns (including genes) from a CopyNumberArray"""
    return zip(cnarr['chromosome'], cnarr['start'], cnarr['end'], cnarr['gene'])


def _ref_split_targets(ref_arr):
    """Split reference into 2 sub-arrays of targets/antitargets."""
    is_bg = (ref_arr['gene'] == 'Background')
    targets = ref_arr[~is_bg]
    antitargets = ref_arr[is_bg]
    return targets, antitargets
