Tumor heterogeneity
===================

DNA samples extracted from solid tumors are rarely completely pure. Stromal or
other normal cells and distinct subclonal tumor-cell populations are typically
present in a sample, and can confound attempts to fit segmented log2 ratio
values to absolute integer copy numbers.

CNVkit provides several points of integration with existing tools and methods
for dealing with tumor heterogeneity and normal-cell contamination.


Inferring tumor purity and subclonal population fractions
---------------------------------------------------------

The third-party program `THetA2 <http://compbio.cs.brown.edu/projects/theta/>`_
can be used to estimate tumor cell content and infer integer copy number of
tumor subclones in a sample.  CNVkit provides wrappers for exporting segments to
THetA2's input format and importing THetA2's result file as CNVkit's segmented
.cns files.

.. We are also working on similar wrappers for related programs including PyLOH.

Using CNVkit with THetA2
````````````````````````

THetA2's input file is a BED-like file, typically with the extension ``.input``,
listing the read counts  within each copy-number segment in a pair of tumor and
normal samples.
CNVkit can generate this file given the CNVkit-inferred tumor segmentation
(.cns) and normal copy log2-ratios (.cnr) or copy number reference file (.cnn).
This bypasses the initial step of THetA2, CreateExomeInput, which counts the
reads in each sample's BAM file.

After running the CNVkit :doc:`pipeline` on a sample, create the THetA2 input file::

    # From a paired normal sample
    cnvkit.py export theta Sample_Tumor.cns Sample_Normal.cnr -o Sample.theta2.input
    # From an existing CNVkit reference
    cnvkit.py export theta Sample_Tumor.cns reference.cnn -o Sample.theta2.input

Then, run THetA2 (assuming the program was unpacked at ``/path/to/theta2/``)::

    # Generates Sample.theta2.BEST.results:
    /path/to/theta2/bin/RunTHetA Sample.theta2.input
    # Parameters for low-quality samples:
    /path/to/theta2/python/RunTHetA.py Sample.theta2.input -n 2 -k 4 -m .90 --FORCE --NUM_PROCESSES `nproc`

Finally, import THetA2's results back into CNVkit's .cns format, matching the
original segmentation (.cns) to the THetA2-inferred absolute copy number
values.::

    cnvkit.py import-theta Sample_Tumor.cns Sample.theta2.BEST.results

THetA2 adjusts the segment log2 values to the inferred cellularity of each
detected subclone; this can result in one or two .cns files representing
subclones if more than one clonal tumor cell population was detected. THetA2
also performs some significance testing of each segment representing a CNA, so
there may be fewer segments derived from THetA2 than were originally found by
CNVkit.

The segment values are still log2-transformed in the resulting .cns files, for
convenience in plotting etc. with CNVkit. These files are also easily converted
to other formats using the :ref:`export` command.



Adjusting copy ratios and segments for normal cell contamination
----------------------------------------------------------------

CNVkit's :ref:`rescale` command uses an estimate of tumor fraction (from
any source) to directly rescale segment log2 ratio values to the value that
would be seen a completely pure, uncontaminated sample. Example with tumor
purity of 60% and a male reference::

    cnvkit.py rescale Sample.cns --purity 0.6 -y -o Sample.rescale.cns

CNVkit's :ref:`call` command then converts the segmented log2 ratio estimates to
absolute integer copy numbers. Note that the rescaling step is optional; either
way, hard thresholds can be used::

    # With CNVkit's default cutoffs
    cnvkit.py call -m threshold Sample.cns -y -o Sample.call.cns
    # Or, using a custom set of cutoffs
    cnvkit.py call -t=-1.1,-0.4,0.3,0.7 Sample.cns -y -o Sample.call.cns

Alternatively, if the tumor cell fraction is known confidently, then use the
``clonal`` method to simply round the log2 ratios to the nearest integer copy
number::

    cnvkit.py call -m clonal Sample.cns -y --purity 0.65 -o Sample.call.cns
    # Or, if already rescaled
    cnvkit.py call -m clonal Sample.rescale.cns -y -o Sample.call.cns

Export integer copy numbers as BED or VCF
-----------------------------------------

The :ref:`export` ``bed`` command emits integer copy number calls in standard
BED format::

    cnvkit.py export bed Sample.call.cns -y -o Sample.bed
    cnvkit.py export vcf Sample.call.cns -y -o Sample.vcf

The rounding of the .cns file's log2 ratios to integer copy numbers here is the
same as in the :ref:`call` command with the ``clonal`` method.
