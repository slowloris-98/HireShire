# My Projects Portfolio

---

## Work Experience

---

### Software Intern — Motherson Technologies, India
**Duration:** Dec 2019 – Apr 2020

#### Description
Designed and developed a full-stack Financial Analytics Platform to help stakeholders process, manage, and audit large volumes of tax receipt data. The frontend was built with HTML, CSS, and JavaScript, delivering an interactive dashboard with dynamic data visualization modules. The backend was powered by Java Spring MVC, connected to a PostgreSQL database capable of handling 200,000+ tax receipts. The platform replaced manual audit review processes with structured, queryable views — streamlining workflows and accelerating resolution efficiency for internal stakeholders.

#### Contributions
- Built the full frontend using HTML, CSS, and JavaScript — including responsive dashboard layouts and interactive data visualization modules for tax receipt trends and audit insights
- Designed and implemented the Java Spring MVC backend, handling request routing, business logic, and server-side data processing
- Architected the PostgreSQL database to store and query 200,000+ tax receipts efficiently
- Integrated the JavaScript frontend with the Spring MVC backend via RESTful endpoints to enable real-time filtering and reporting
- Streamlined audit workflows by replacing manual review processes with structured, queryable dashboard views

#### Impact & Results
- Processed and visualized **200,000+ tax receipts** via the platform
- Accelerated audit resolution efficiency by **14%** for stakeholders through interactive visualizations and streamlined workflows

#### Tools / Frameworks / Languages
- **Frontend:** HTML, CSS, JavaScript (interactive dashboard and data visualization)
- **Backend:** Java, Spring MVC
- **Database:** PostgreSQL
- **Architecture:** Full-stack MVC web application
- **Domain:** Financial analytics, tax data processing, audit workflow automation

---

### Software Engineer — Accenture, Bengaluru, India
**Associate Software Engineer:** Nov 2020 – Nov 2021
**Software Engineer:** Dec 2021 – Jul 2024
**Awards:** ACE (Excellence) Award & Contribution Award for creating exceptional value in key client growth priority areas; promoted to Software Engineer via Accenture's fast-track process based on performance

#### Description
Worked as a Software Engineer at Accenture building cloud-native, serverless solutions on AWS for enterprise clients. Core work spanned designing and delivering microservices-based platforms across notification systems, cloud storage optimization, security remediation, server operations, and internal developer tooling — all on AWS serverless architecture integrated with Azure DevOps CI/CD pipelines. Also contributed to an internal ChatOps platform and a recommendation engine for automation assets, directly reducing operational overhead for cross-functional teams. Recognized with the ACE (Excellence) Award and Contribution Award, and promoted via the fast-track process ahead of the standard cycle based on consistent high performance.

#### Contributions
- Architected a user notification system using AWS API Gateway, Lambda, and DynamoDB to deliver real-time remediation stage notifications to end users via a microservices-based design
- Optimized cloud storage by redesigning DynamoDB access patterns — including schema, partition keys, and sort keys — and refactoring associated CRUD microservices to reduce retrieval latency
- Delivered a code vulnerability remediation solution using a serverless microservices architecture with AWS Lambda, S3, and EventBridge integrated with Azure DevOps, safeguarding 400+ applications across organizations
- Designed a single-touch server patching solution to autonomously resolve ServiceNow alerts using serverless architecture (AWS + Azure DevOps), dramatically reducing manual intervention in the patching cycle
- Partnered with cross-functional teams to build a ChatOps platform that routes natural language queries to automation workflows via integrated serverless microservices, replacing a high-overhead manual process
- Developed an analytics-driven recommendation engine for 100+ automation assets, implementing weighted scoring logic to personalize tool suggestions and reduce time users spend discovering relevant automations
- Built automation solutions and custom tasks for CI/CD pipelines using Azure DevOps, streamlining deployment cycles and reducing mean time to resolve pipeline failures

#### Impact & Results
- **20% increase in DAU** post-launch of the user notification system
- **37% reduction in DynamoDB retrieval latency** through access pattern redesign and CRUD microservice optimization
- **400+ applications** protected across organizations via the serverless code vulnerability remediation solution
- **80% reduction in Mean Time to Resolution (MTTR)** for server patching via the single-touch patching solution
- **40% reduction in operational overhead** for the ChatOps platform, cutting average query resolution time from ~5 minutes to ~1 minute
- **30% reduction in automation asset discovery time** through the personalized recommendation engine
- Awarded the **ACE (Excellence) Award** and **Contribution Award** for creating exceptional value in key client growth priority areas
- **Promoted to Software Engineer via fast-track process** ahead of the standard promotion cycle, based on performance

#### Tools / Frameworks / Languages
- **Cloud:** AWS (API Gateway, Lambda, DynamoDB, S3, EventBridge)
- **DevOps / CI/CD:** Azure DevOps (pipelines, custom tasks, automation)
- **Architecture:** Serverless microservices, event-driven architecture
- **Integrations:** ServiceNow (alert-driven automation), ChatOps workflows
- **Domain:** Cloud infrastructure, DevSecOps, AIOps, enterprise automation

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
- Implemented architecture-based transfer learning: recreated the HAR-CNN architecture (Conv1D → MaxPooling → Conv1D → GlobalMaxPooling → Dense) and fine-tuned it on MMASH with zero-padded inputs of shape (200, 3)
- Conducted data exploration and visualization notebooks separately before modelling
- Wrote the full project report (CS256_ProjectReport_Udayan.pdf)

---

### Impact & Results
- **Best accuracy: ~56%** achieved by Random Forest and CNN models
- LSTM demonstrated strong user-level generalization under the LOSO evaluation protocol
- Architecture-based transfer learning from UCI HAR to MMASH showed effective domain adaptation without requiring pre-trained weights
- Evaluated across 4 metrics: Accuracy, Precision, Recall, and F1-Score
- Used 5-fold cross-validation in addition to LOSO for robust performance estimation

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
- Configured appsettings.json for environment-specific MongoDB connection strings and API keys

---

### Impact & Results
- Fully functional REST API with secure authentication — ready for frontend or mobile client integration
- Dual-mode weather lookup (city name or auto IP geolocation) improves usability without requiring explicit user input
- JWT authentication ensures stateless, scalable security with no server-side session storage

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

## Project 4: Natural Language Inference using QLoRa

**Repository:** https://github.com/slowloris-98/NSP_QLoRa

---

### Description
A rigorous comparative benchmark study investigating whether parameter-efficient fine-tuning (PEFT) of a large generative LLM can match purpose-built discriminative models on a structured classification task. The central research question: can updating fewer than 0.2% of a 7.2B-parameter generative model's weights match a task-specific 355M discriminative baseline?

The task is **Natural Language Inference (NLI)** — given a premise and hypothesis, classify their relationship as Entailment, Neutral, or Contradiction. The dataset is **GLUE MNLI** (15,000 training samples, 1,000 validation samples). Four models are benchmarked end-to-end: RoBERTa-large-mnli (discriminative baseline), Mistral-7B zero-shot, Mistral-7B + QLoRA, and Mistral-7B + DoRA. Key engineering challenges include loading a 7.2B parameter model on a single Colab GPU using 4-bit NF4 quantization via bitsandbytes (reducing VRAM from ~28 GB to ~3.5 GB), and designing a prompt-parsing pipeline to extract discrete class labels from free-form generated text.

---

### My Contributions
- Designed the full experimental setup: task formulation, dataset selection (GLUE MNLI), prompt format, and evaluation protocol
- Implemented 4-bit NF4 quantization via bitsandbytes to enable 7B-parameter model training on a single A100/T4 GPU in Google Colab, reducing GPU memory footprint by ~87% (from ~28 GB to ~3.5 GB)
- Configured and trained QLoRA (rank=16, alpha=32, targeting q/k/v/o projection layers, lr=2e-4, 2 epochs, effective batch size=32, paged AdamW 8-bit optimizer, BF16 mixed precision, gradient checkpointing)
- Configured and trained DoRA (identical to QLoRA but with use_dora=True and lr halved to 1e-4 to account for DoRA's sensitivity to larger weight updates)
- Built separate evaluation scripts for all four models (evaluate_roBERTa.py, evaluate_baseline_llm.py, evaluate_finetuned_llm.py) and a dataset utility (dataset.py) for MNLI loading and prompt formatting
- Trained and saved LoRA adapter weights (.safetensors) for both QLoRA and DoRA variants
- Wrote three detailed code and reasoning walkthroughs: nli_reasoning_walkthrough.md, nsp_classification_walkthrough.md, nsp_qlora_code_walkthrough.md
- Conducted multiple timestamped evaluation runs and systematically stored results in results/

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
- **PEFT Methods:** QLoRA, DoRA (via Hugging Face peft)
- **Quantization:** 4-bit NF4 via bitsandbytes
- **Training Framework:** trl SFTTrainer, Hugging Face transformers Trainer API
- **Dataset:** GLUE MNLI (Hugging Face datasets)
- **Libraries:** PyTorch, Transformers, PEFT, BitsAndBytes, TRL, Accelerate, Scikit-learn
- **Compute:** Google Colab (A100 / T4 GPU)
- **Notebook:** Jupyter Notebook

---

## Project 5: Evaluator-Optimizer RAG Framework

**Repository:** https://github.com/slowloris-98/Evaluator-Optimizer-RAG
**Publication:** Accepted at an IEEE Conference

---

### Description
A feedback-driven Retrieval-Augmented Generation (RAG) pipeline that uses cross-LLM reflection to iteratively improve answer quality on video-based MMLU questions. Rather than relying on a single LLM for generation and evaluation, the system separates these roles across three frontier models: **GPT-4o** generates the base answer, **Gemini** acts as a feedback/critic LLM that reviews and refines it, and **Llama 3.3-70B** serves as the independent evaluator scoring outputs using RAGAS metrics.

The pipeline is benchmarked across four retrieval configurations combining two embedding models (BGE-base-en-v1.5, all-MiniLM-L6-v2) with two retrieval strategies (Similarity Search, MMR — Maximal Marginal Relevance). The dataset spans 30 video lectures across three STEM subjects (Chemistry, Physics, Math — 10 videos each), with transcripts forming the retrieval corpus. Evaluation spans seven RAGAS metrics: Faithfulness, Answer Relevancy, Accuracy, Response Groundedness, Semantic Similarity, String Similarity, and Context Recall. The work was accepted at an IEEE Conference.

---

### My Contributions
- Designed the full cross-reflective RAG architecture: base generation (GPT-4o) → feedback/refinement (Gemini) → independent evaluation (Llama 3.3-70B via RAGAS)
- Built the data preparation pipeline (video_mmlu_data_preparation.ipynb) to preprocess 30 video lectures across Chemistry, Physics, and Math, extracting transcripts and producing the curated QA dataset
- Implemented the main RAG pipeline (evaluator_optimizer_rag.ipynb) covering: document ingestion, chunking, embedding, vector store retrieval, base answer generation, cross-LLM feedback loop, and RAGAS evaluation
- Benchmarked all four retrieval configurations: BGE + Similarity, BGE + MMR, MiniLM + Similarity, MiniLM + MMR
- Built the results aggregation pipeline (generate_summary_csvs.ipynb) to compile per-video, per-subject, and cross-embedding comparison summaries with timestamped outputs
- Designed and included the system architecture diagram (evaluator-optimizer-RAG-architecture.png)
- Wrote and submitted the research paper, which was accepted at an IEEE Conference

---

### Impact & Results
Evaluated across 30 videos (Chemistry, Physics, Math) and 4 retrieval configurations.

Overall performance averaged across all 4 configurations (Base → Feedback):
- **Faithfulness:** 0.802 → 0.913 (+0.111)
- **Response Groundedness:** 0.879 → 0.988 (+0.109), reaching near-perfect 0.99
- **Answer Relevancy:** 0.839 → 0.890 (+0.051)
- **Accuracy:** 0.671 → 0.717 (+0.046)
- **Semantic Similarity:** 0.856 → 0.874 (+0.018)
- **Context Recall:** 0.964 → 0.964 (stable — retrieval quality unchanged, as expected)

Best single configuration — **BGE + MMR**: Faithfulness 0.797 → 0.929 (+0.132), Accuracy 0.666 → 0.723 (+0.057)

Best subject-wise faithfulness improvements:
- Chemistry (BGE + MMR): +0.178 Faithfulness, +0.090 Accuracy
- Physics (MiniLM + MMR): +0.142 Faithfulness
- Math (MiniLM + MMR): +0.103 Faithfulness, +0.045 Accuracy

**Key finding:** Cross-LLM feedback (Gemini critiquing GPT-4o) consistently improves all generative quality metrics across every configuration and subject, with the largest gains in Faithfulness (~+11pp) and Response Groundedness (~+11pp).

---

### Tools / Frameworks / Languages
- **Language:** Python
- **LLMs:** GPT-4o (OpenAI) — base generation; Gemini (Google) — feedback/optimizer; Llama 3.3-70B (Groq) — evaluator
- **RAG Framework:** LangChain (vector store, retrieval, pipeline orchestration)
- **Evaluation:** RAGAS (Faithfulness, Answer Relevancy, Accuracy, Response Groundedness, Semantic Similarity, String Similarity, Context Recall)
- **Embeddings:** BGE-base-en-v1.5, all-MiniLM-L6-v2
- **Retrieval Strategies:** Similarity Search, MMR (Maximal Marginal Relevance)
- **Dataset:** Video-based MMLU — 30 videos across Chemistry, Physics, and Math
- **APIs:** OpenAI API, Google AI API (Gemini), Groq API (Llama)
- **Libraries:** LangChain, RAGAS, Pandas, Python-dotenv
- **Notebook:** Jupyter Notebook
- **Publication:** IEEE Conference (accepted)

---

## Project 6: Agentic Infrastructure Observability

**Repository:** https://github.com/slowloris-98/Agentic-Oberservability-BMC
**Demo:** https://www.youtube.com/watch?v=YqEp3CP8ePM
**Hackathon:** Won first prize

---

### Description
A full-stack agentic AI platform for intelligent, autonomous monitoring and remediation of data center hardware. Built for the **Axiado Hackathon**, the system addresses a core gap in legacy infrastructure management: traditional tools are reactive, fragmented, and require heavy manual intervention. This platform couples deep observability with autonomous LLM-driven control — hardware telemetry is continuously ingested, an AI agent reasons over it, and corrective actions are triggered autonomously with minimal human input.

The architecture spans seven integrated layers: a mocked **Redfish API** (the industry standard BMC interface) for hardware telemetry simulation and control, a **FastAPI backend** for data ingestion and rule-based classification, **Prometheus + Grafana** for real-time metrics and dashboards embedded in the UI, **MongoDB + S3** for historical storage and log archival, a **LangChain + Gemini AI** agent supporting both inference queries ("What faults occurred between timestamps?") and action queries ("Set fan speed to 100%"), and a **React + TypeScript** frontend with a conversational chat panel, real-time log stream, and embedded Grafana dashboards.

---

### My Contributions
- Contributed across 107 commits to a production-quality full-stack agentic system
- Co-designed the overall system architecture (documented in AxiadoHackathonArchitecture.png)
- Worked with the Redfish API mock layer to simulate real BMC hardware telemetry and control endpoints
- Implemented and contributed to the LangChain + Gemini AI agent supporting dual query modes: inference (historical data lookups via MongoDB/S3) and action (hardware control via Redfish endpoints)
- Built and contributed to the FastAPI backend for telemetry ingestion, rule-based signal classification, SSE log streaming, and Prometheus /metrics exposure
- Integrated Prometheus scraping (5-second polling) and Grafana dashboard embedding into the React UI

---

### Impact & Results
- End-to-end autonomous infrastructure management: telemetry ingestion → AI reasoning → hardware control actions, all within a single unified interface
- **Two-mode AI agent** supporting natural language inference (historical analysis) AND real-time hardware action commands via Redfish — a significant step beyond passive dashboards
- Fully mocked Redfish API built from scratch, enabling realistic simulation of BMC-level controls (fan speeds, voltage thresholds, PSU power limits) without requiring physical hardware
- Prometheus + Grafana metrics pipeline delivering 5-second refresh real-time visibility, embedded directly in the React UI
- Real-time log streaming via Server-Sent Events (SSE) for live operational situational awareness
- **107 commits** across the team reflecting sustained, production-quality development effort

---

### Tools / Frameworks / Languages
- **Languages:** Python, TypeScript
- **Backend:** FastAPI (telemetry ingestion, rule-based classification, SSE log streaming, Prometheus metrics endpoint)
- **AI / LLM Agent:** LangChain + Gemini AI (inference & action query modes)
- **Hardware Interface:** Redfish API (mocked from scratch — BMC telemetry & control)
- **Observability Stack:** Prometheus (metrics scraping), Grafana (real-time dashboards, embedded in UI)
- **Storage:** MongoDB (transactional/historical telemetry), AWS S3 (log archival and snapshots)
- **Frontend:** React + TypeScript (chat panel, real-time logs, embedded Grafana, query filters)
- **Containerization:** Docker
- **Domain:** AIOps, infrastructure observability, autonomous remediation, data center management

---
