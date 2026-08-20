---
license: cc-by-4.0
---

<a name="readme-top"></a>

# Machine Learning-based Detection of Concept Drifts in Business Processes
<sub>
written by <a href="mailto:alexander.kraus@uni-mannheim.de">Alexander Kraus</a><br />
</sub>

## About
This repository contains the implementation, data, evaluation scripts, and results as described in the manuscript
<i>Machine Learning-based Detection of Concept Drifts in Business Processes</i> 
by A. Kraus and H. van der Aa, submitted for consideration in the BPMâ€™24 Special Collection of the Process Science journal. 

This work is an extended version of the original paper:
<i>Looking for Change: A Computer Vision Approach for Concept Drift Detection in Process Mining</i> 
by A. Kraus and H. van der Aa, accepted for the <i>22nd Business Process Management Conference 2024 in Krakow</i>.


## Abstract
Concept drift in process mining occurs when a single event log includes data from multiple versions of a process, making the detection of such drifts essential for ensuring reliable process mining results. 
Although many techniques have been proposed, they exhibit limitations in accuracy and scope. 
Specifically, their accuracy diminishes when facing noise, varying drift types, or different levels of change severity.
Additionally, these techniques primarily focus on detecting sudden and gradual drifts, overlooking the automated detection of incremental and recurring drifts.
To address these limitations, we present \texttt{CV4CDD-4D}, a novel approach for automated concept drift detection that can identify sudden, gradual, incremental, and recurring drifts. 
Our approach follows an entirely different paradigm. Specifically, it employs a supervised machine learning model fine-tuned on a large collection of event logs with known concept drifts, enabling the model to learn how drifts manifest in event logs.
The possibility to train such a model has recently emerged through a tool that generates event logs with known concept drifts. 
However, applying supervised machine learning remains challenging due to the complexities of encoding. 
To address this, we propose converting an event log into an image-based representation that captures process evolution over time, enabling the use of a state-of-the-art computer vision model to detect drifts. 
Our experiments show that our approach, compared to existing solutions, improves the accuracy and robustness of drift detection while extending coverage to a broader range of drift types, highlighting the potential of this new paradigm.
![Alt text](approaches/approach_overview.png)


<!-- GETTING STARTED -->
## Setup
To run the approach, follow these steps.


### Prerequisites
For full functionality, this repository requires the following software:
* Python 3.9
* TensorFlow Model Garden ([clone here](https://github.com/tensorflow/models))
* [poetry](https://python-poetry.org/) -> for packaging/dependency management, see their website for installation and usage


### Installation
1. Clone the repo
2. Go into the project root directory
3. Install dependencies with poetry: 'poetry install'. This creates a virtual environment with the corresponding dependencies.

Optional:

The fine-tuned model comes from the Model Garden for TensorFlow:
Clone the repo into the folder "models" inside the root directory: git clone https://github.com/tensorflow/models.git
Originally pulled based on the commit: 3256e1018a402bf30179ffa9b82e01024fa61fc2, Author: mjyun01 <87511647+mjyun01@users.noreply.github.com>, Date:   Fri Jan 26 09:19:30 2024 +0900

To execute run_prodrift.py, create a folder "ProDrift2.5" inside the root directory and place there the ProDrift2.5.jar file from https://apromore.com/research-lab


<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- USAGE EXAMPLES -->
## Usage

To use a pretrained computer-vision model, use the [predict](approaches/object_detection/predict.py) script.
Always start a poetry shell, if you use the terminal:
```sh
   poetry shell
```
```sh
   cd approaches/object_detection
```
```sh
   python predict.py --model-path <specify path of unzipped pretrained model> --log-dir <specify directory where event logs are stored> --encoding winsim --n-windows 200 --output-dir <specify output directory>
```

The script outputs not only the visual detection of the drift types, but also a detailed report that specifies the drift moments on traces.

The configuration file can be found [here](approaches/object_detection/utils/config.py). 
All configuration variables are explained in detail [here](approaches/config_doc.md). 

### Evaluation
Results from the evaluation can be found [here](EvaluationResults/CV4CDD_4D).

### Data and fine-tuned models
All datasets and fine-tuned CV4CDD-4D models are available for download [here](https://huggingface.co/datasets/pm-science/cv4cdd_4d/tree/main).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## References

The original version of the repository (https://github.com/jkoessle/ODCD-Framework) was created by Jonathan Kössler within his Master Thesis 
"Object Detection for Concept Drift - A Deep Learning Framework for Concept Drift Detection in Process Mining", 2023, University of Mannheim.



<!-- LICENSE -->
## License

2026 Alexander Kraus, University of Mannheim

This work is licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0).

You are free to share and adapt this work for any purpose, including commercial use, provided that appropriate credit is given to the authors.

See `LICENSE.txt` for the full license text.

<p align="right">(<a href="#readme-top">back to top</a>)</p>