# My Projects Portfolio

---

## Work Experience

---

### Software Intern — Motherson Technologies, India
**Duration:** Dec 2019 – Apr 2020

#### Description
Designed and developed a Financial Analytics Platform to help stakeholders process, manage, and audit large volumes of tax receipt data. The platform was built on a Java Spring MVC architecture with a PostgreSQL backend capable of handling 200,000+ tax receipts. Interactive data visualization modules were integrated into the dashboard to surface insights and streamline audit workflows, reducing the manual effort required for financial reviews and accelerating resolution efficiency for internal stakeholders.

#### Contributions
- Designed and built a Java Spring MVC web dashboard as the primary interface for financial analytics
- Architected the PostgreSQL database to store and query 200,000+ tax receipts efficiently
- Developed interactive data visualization modules to surface trends, anomalies, and audit-relevant insights from the tax receipt data
- Integrated the visualization layer with the backend to enable real-time filtering and reporting for stakeholders
- Streamlined audit workflows by replacing manual review processes with structured, queryable dashboard views

#### Impact & Results
- Processed and visualized **200,000+ tax receipts** via the platform
- Accelerated audit resolution efficiency by **14%** for stakeholders through interactive visualizations and streamlined workflows

#### Tools / Frameworks / Languages
- **Language:** Java
- **Framework:** Spring MVC
- **Database:** PostgreSQL
- **Frontend:** Interactive data visualization modules (dashboard UI)
- **Domain:** Financial analytics, tax data processing, audit workflow automation

---

## Project 1: DDoS Attack Detection Using Reinforcement Learning

**Repository:** https://github.com/slowloris-98/DDoS_RL

---

### Description
An adaptive Intrusion Detection System (IDS) that reframes DDoS attack detection as a reinforcement learning problem. Instead of traditional rule-based or supervised classifiers, a Deep Q-Network (DQN) agent is trained to observe network traffic features and decide whether a connection is benign or a DDoS attack. The environment is built as a custom OpenAI Gym environment on top of the NSL-KDD benchmark dataset — a widely used dataset in network intrusion detection research.

The agent learns through trial and error: it receives positive rewards for correct classifications and negative rewards for misclassifications, gradually improving its policy over training episodes. The project also includes visualizations of cumulative reward trends and epsilon decay (the shift from random exploration to learned exploitation).

---

### My Contributions
- Designed and implemented the custom OpenAI Gym environment wrapping the NSL-KDD dataset
- Built and trained the Deep Q-Network (DQN) agent using PyTorch
- Performed data preprocessing and feature engineering on the NSL-KDD dataset (label encoding, normalization)
- Implemented the reward function, replay buffer, and epsilon-greedy exploration strategy
- Generated training visualizations (cumulative reward curves, epsilon decay plots)
- Prepared the project presentation (CS258_Project_presentation.pptx)

---

### Impact & Results
- The DQN agent successfully learned to distinguish benign from DDoS traffic over training episodes
- Cumulative reward increased steadily across episodes, demonstrating convergence of the learned policy
- Epsilon decayed from 1.0 toward a minimum, confirming the agent transitioned from exploration to exploitation
- **[Add specific metrics if available: final detection accuracy %, F1 score, average reward at convergence, number of training episodes, etc.]**

---

### Tools / Frameworks / Languages
- **Language:** Python
- **ML Framework:** PyTorch
- **RL Environment:** OpenAI Gym (custom environment)
- **Dataset:** NSL-KDD (Network Security Lab - Knowledge Discovery and Data Mining)
- **Libraries:** NumPy, Pandas, Scikit-learn, Matplotlib
- **Notebook:** Jupyter Notebook / Google Colab

---

## Project 2: Human Activity Recognition Using MMASH Dataset

**Repository:** https://github.com/slowloris-98/HAR_MMASH

---

### Description
A machine learning and deep learning project for Human Activity Recognition (HAR) using the MMASH (MultiModal Activities for Stress and Health) dataset — a real-world dataset collected from wearable sensors. The goal is to classify user activities (e.g., walking, sitting, standing) from multimodal sensor data including accelerometer axes, heart rate, step count, and inclinometer readings.

The project covers the full ML pipeline: signal preprocessing (Butterworth low-pass filtering, z-score normalization, linear interpolation for missing data), time and frequency domain feature engineering, model development across both classical ML and deep learning approaches, and rigorous evaluation using k-Fold cross-validation and Leave-One-Subject-Out (LOSO) — the gold standard for generalization testing in wearable computing. A transfer learning component was also implemented by adapting a pretrained CNN architecture (HAR-CNN trained on the UCI HAR dataset) to the MMASH domain.

---

### My Contributions
- Performed full data preprocessing pipeline: handled missing values via linear interpolation, applied Butterworth low-pass filtering for noise reduction, and applied z-score normalization
- Segmented time-series data into 1-second overlapping windows at 50 Hz for model input
- Engineered time-domain features (mean, std, min, max, peak-to-peak, skewness, kurtosis, signal magnitude area) and frequency-domain features (dominant frequency, signal energy via FFT)
- Built and evaluated baseline models: Decision Tree, K-Nearest Neighbors, Naive Bayes
- Built and evaluated advanced models: Random Forest, SVM, CNN, and LSTM using Keras
- Implemented Leave-One-Subject-Out (LOSO) cross-validation for user-level generalization testing
- Implemented architecture-based transfer learning: recreated the HAR-CNN architecture (`Conv1D → MaxPooling → Conv1D → GlobalMaxPooling → Dense`) and fine-tuned it on MMASH with zero-padded inputs of shape `(200, 3)`
- Conducted data exploration and visualization notebooks separately before modelling
- Wrote the full project report (`CS256_ProjectReport_Udayan.pdf`)

---

### Impact & Results
- **Best accuracy: ~56%** achieved by Random Forest and CNN models
- LSTM demonstrated strong user-level generalization under the LOSO evaluation protocol
- Architecture-based transfer learning from UCI HAR to MMASH showed effective domain adaptation without requiring pre-trained weights
- Evaluated across 4 metrics: Accuracy, Precision, Recall, and F1-Score
- Used 5-fold cross-validation in addition to LOSO for robust performance estimation
- **[Add any additional specifics if available: number of activity classes, number of subjects in MMASH, dataset size, best F1 score, etc.]**

---

### Tools / Frameworks / Languages
- **Language:** Python
- **Deep Learning:** Keras (TensorFlow backend) — CNN, LSTM
- **Classical ML:** Scikit-learn — Random Forest, SVM, Decision Tree, K-NN, Naive Bayes
- **Signal Processing:** SciPy (Butterworth low-pass filter), NumPy (FFT)
- **Dataset:** MMASH (MultiModal Activities for Stress and Health), UCI HAR (for transfer learning source architecture)
- **Libraries:** Pandas, NumPy, Matplotlib, SciPy, Scikit-learn
- **Notebook:** Jupyter Notebook

---

## Project 3: WeatherAPI – Real-Time Weather Forecasting Web API

**Repository:** https://github.com/slowloris-98/WeatherAPI

---

### Description
A production-style RESTful Web API built with .NET Core that delivers real-time weather information to clients. The API supports two modes of weather lookup: city-specific queries where the user provides a city name, and automatic geolocation-based retrieval where the API infers the user's location from their IP address and returns local weather data accordingly.

The project goes beyond a simple weather wrapper — it includes a full user authentication system with JWT (JSON Web Tokens) for secure registration and login, and integrates MongoDB for persistent user data storage. The architecture follows clean separation of concerns with dedicated Controllers, Models, and a Service layer for business logic.

---

### My Contributions
- Designed and built the full .NET Core Web API from scratch following RESTful principles
- Implemented JWT-based user authentication — registration, login, and token issuance/validation
- Built IP geolocation-based automatic weather retrieval for seamless user experience
- Implemented city-specific weather query endpoints
- Integrated MongoDB as the persistent data store for user management
- Structured the project with a clean layered architecture: Controllers → Services → Models
- Configured `appsettings.json` for environment-specific MongoDB connection strings and API keys

---

### Impact & Results
- Fully functional REST API with secure authentication — ready for frontend or mobile client integration
- Dual-mode weather lookup (city name or auto IP geolocation) improves usability without requiring explicit user input
- JWT authentication ensures stateless, scalable security with no server-side session storage
- **[Add specifics if available: number of endpoints, average response time, any deployment details (Azure, Docker, etc.), weather data provider used (OpenWeatherMap, etc.)]**

---

### Tools / Frameworks / Languages
- **Language:** C#
- **Framework:** .NET Core Web API
- **Database:** MongoDB
- **Authentication:** JWT (JSON Web Tokens)
- **Architecture:** RESTful API, Service Layer pattern
- **IDE:** Visual Studio
- **Other:** appsettings.json for configuration management

---

## Project 5: Loan Disbursement Amount Prediction

**Repository:** https://github.com/slowloris-98/loan_amount_prediction

---

### Description
A machine learning project that predicts the optimal loan amount to disburse to an applicant based on demographics, loan purpose, regional data, market trends, and income features. The project frames loan prediction as a regression problem — not just approving or rejecting a loan, but determining the right dollar amount to offer each applicant.

The dataset used is the **BigQuery Fintech Dataset** from Kaggle, which spans six relational tables: customer information, historical loan disbursements, year-over-year loan count trends, loan purposes, regional loan data, and state-to-region geographic mappings. An ETL pipeline (in both SQL and Databricks notebook formats) was built to join, clean, and prepare the data before modelling. Five models were implemented and benchmarked — Linear Regression, Decision Tree, Random Forest, Multi-Layer Perceptron (MLP), and RNN (LSTM) — to comprehensively compare classical and deep learning approaches on a fintech regression task.

---

### My Contributions
- Designed and implemented the full ETL pipeline (`ETL.sql` and `ETL.dbc`) to extract, join, and transform data across six Kaggle BigQuery tables
- Performed data preprocessing: dropped columns with >50% missing values, removed duplicates, excluded null string records, applied z-score normalization, and one-hot encoded categorical variables
- Built and experimented with data visualizations: loan amount by loan type distribution and total loan amount by region
- Implemented and tuned five separate models in individual notebooks: Linear Regression (`Regression.ipynb`), Decision Tree (`Decision Tree.ipynb`), Random Forest (`Random Forest.ipynb`), MLP (`MLP.ipynb`), and RNN/LSTM (`RNN.ipynb`)
- Experimented with decision tree depths ranging from 5 to 20 to fine-tune performance
- Designed the RNN with three stacked LSTM layers (50, 25, 12 units) followed by a single-unit regression output
- Conducted cross-model comparative evaluation on a 70-30 train-test split
- Wrote the project conclusion and analysis, documenting which models performed best and why

---

### Impact & Results
- **Linear Regression** performed competitively against tree and neural network models, indicating strong linear correlations in the fintech dataset
- **Decision Tree** and **Random Forest** achieved comparable results, with Random Forest producing smoother, more generalizable predictions due to ensemble averaging
- **RNN/LSTM** confirmed the absence of sequential or temporal patterns in the data — a valuable null result that validates the dataset's structure
- Five models benchmarked end-to-end on the same dataset, enabling a rigorous, apples-to-apples comparison
- **[Add specific metric values from the evaluation table in your README: R², RMSE, MAE for each model — this is the most important thing to fill in here]**

---

### Tools / Frameworks / Languages
- **Language:** Python, SQL
- **ETL:** Apache Spark / Databricks (`.dbc` notebook), SQL (`ETL.sql`)
- **Deep Learning:** Keras/TensorFlow — RNN with LSTM layers, MLP
- **Classical ML:** Scikit-learn — Linear Regression, Decision Tree, Random Forest
- **Dataset:** BigQuery Fintech Dataset (Kaggle) — 6 relational tables
- **Libraries:** Pandas, NumPy, Matplotlib, Scikit-learn, Keras
- **Notebook:** Jupyter Notebook

---

## Project 6: NSP_QLoRa – Fine-Tuning Mistral-7B for Natural Language Inference

**Repository:** https://github.com/slowloris-98/NSP_QLoRa

---

### Description
A rigorous comparative benchmark study investigating whether parameter-efficient fine-tuning (PEFT) of a large generative LLM can match purpose-built discriminative models on a structured classification task. The central research question: *can updating fewer than 0.2% of a 7.2B-parameter generative model's weights match a task-specific 355M discriminative baseline?*

The task is **Natural Language Inference (NLI)** — given a premise and hypothesis, classify their relationship as Entailment, Neutral, or Contradiction. The dataset is **GLUE MNLI** (15,000 training samples, 1,000 validation samples). Four models are benchmarked end-to-end: RoBERTa-large-mnli (discriminative baseline), Mistral-7B zero-shot, Mistral-7B + QLoRA, and Mistral-7B + DoRA. Key engineering challenges include loading a 7.2B parameter model on a single Colab GPU using 4-bit NF4 quantization via `bitsandbytes` (reducing VRAM from ~28 GB to ~3.5 GB), and designing a prompt-parsing pipeline to extract discrete class labels from free-form generated text.

---

### My Contributions
- Designed the full experimental setup: task formulation, dataset selection (GLUE MNLI), prompt format, and evaluation protocol
- Implemented 4-bit NF4 quantization via `bitsandbytes` to enable 7B-parameter model training on a single A100/T4 GPU in Google Colab, reducing GPU memory footprint by ~87% (from ~28 GB to ~3.5 GB)
- Configured and trained QLoRA (rank=16, alpha=32, targeting q/k/v/o projection layers, lr=2e-4, 2 epochs, effective batch size=32, paged AdamW 8-bit optimizer, BF16 mixed precision, gradient checkpointing)
- Configured and trained DoRA (identical to QLoRA but with `use_dora=True` and lr halved to 1e-4 to account for DoRA's sensitivity to larger weight updates)
- Built separate evaluation scripts for all four models (`evaluate_roBERTa.py`, `evaluate_baseline_llm.py`, `evaluate_finetuned_llm.py`) and a dataset utility (`dataset.py`) for MNLI loading and prompt formatting
- Trained and saved LoRA adapter weights (`.safetensors`) for both QLoRA and DoRA variants
- Wrote three detailed code and reasoning walkthroughs: `nli_reasoning_walkthrough.md`, `nsp_classification_walkthrough.md`, `nsp_qlora_code_walkthrough.md`
- Conducted multiple timestamped evaluation runs and systematically stored results in `results/`

---

### Impact & Results
- **Mistral-7B + QLoRA: 90.2% accuracy, Macro F1 = 0.90** — outperforming the purpose-built RoBERTa-large-mnli discriminative baseline (89.8% accuracy, F1 0.90) while training only **14.3M out of 7.2B parameters (<0.2% of total weights)**
- **Mistral-7B + DoRA: 88.8% accuracy, F1 = 0.89** — competitive but slightly below QLoRA, indicating DoRA may need more data or epochs to exceed standard LoRA
- **Mistral-7B zero-shot: 36.9% accuracy, F1 = 0.29** — barely above random chance (33.3%), with extreme bias toward predicting "Neutral" (88% recall), demonstrating that strong general reasoning does not transfer to structured classification without fine-tuning
- **4-bit NF4 quantization proven effectively lossless**: ~87% VRAM reduction with negligible accuracy degradation
- Per-class F1 for best model (QLoRA): Entailment 0.89 · Neutral 0.87 · Contradiction 0.94

---

### Tools / Frameworks / Languages
- **Language:** Python
- **Base Model:** Mistral-7B-v0.3 (7.2B parameters, generative LLM)
- **Discriminative Baseline:** RoBERTa-large-mnli (355M parameters)
- **PEFT Methods:** QLoRA, DoRA (via Hugging Face `peft`)
- **Quantization:** 4-bit NF4 via `bitsandbytes`
- **Training Framework:** `trl` SFTTrainer, Hugging Face `transformers` Trainer API
- **Dataset:** GLUE MNLI (Hugging Face `datasets`)
- **Libraries:** PyTorch, Transformers, PEFT, BitsAndBytes, TRL, Accelerate, Scikit-learn
- **Compute:** Google Colab (A100 / T4 GPU)
- **Notebook:** Jupyter Notebook

---
