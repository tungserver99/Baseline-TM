import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from .ECR import ECR
from .GR import GR
from ._ETP import ETP
import torch_kmeans
import logging
import sentence_transformers

def pairwise_euclidean_distance(x, y):
    cost = torch.sum(x ** 2, axis=1, keepdim=True) + torch.sum(y ** 2, dim=1) - 2 * torch.matmul(x, y.t())
    return cost

class FASTOPIC(nn.Module):
    def __init__(self, vocab_size, num_topics=50, num_groups=10,  embed_size=384, 
                 dropout=0., beta_temp=0.2, theta_temp: float=1.0,
                 DT_alpha: float=3.0, TW_alpha: float=2.0):
        super().__init__()

        self.num_topics = num_topics
        self.num_groups = num_groups
        self.beta_temp = beta_temp
        self.DT_alpha = DT_alpha
        self.TW_alpha = TW_alpha
        self.theta_temp = theta_temp

        self.epsilon = 1e-12
        
        # self.simpleembedding = SimpleSentenceEmbeddingNN(input_dim=vocab_size, hidden_dim_1=hidden_dim_1, hidden_dim_2=hidden_dim_2, embedding_dim=embed_size, dropout_prob=dropout)
        
        self.word_embeddings = nn.init.trunc_normal_(torch.empty(vocab_size, embed_size))
        self.word_embeddings = nn.Parameter(F.normalize(self.word_embeddings))

        self.topic_embeddings = torch.empty((self.num_topics, embed_size))
        nn.init.trunc_normal_(self.topic_embeddings, std=0.1)
        self.topic_embeddings = nn.Parameter(F.normalize(self.topic_embeddings))

        self.word_weights = nn.Parameter((torch.ones(vocab_size) / vocab_size).unsqueeze(1))
        self.topic_weights = nn.Parameter((torch.ones(self.num_topics) / self.num_topics).unsqueeze(1))

        self.DT_ETP = ETP(self.DT_alpha, init_b_dist=self.topic_weights)
        self.TW_ETP = ETP(self.TW_alpha, init_b_dist=self.word_weights)

    def get_transp_DT(self,
                      doc_embeddings,
                    ):

        topic_embeddings = self.topic_embeddings.detach().to(doc_embeddings.device)
        _, transp = self.DT_ETP(doc_embeddings, topic_embeddings)

        return transp.detach().cpu().numpy()

    # only for testing
    def get_beta(self):
        _, transp_TW = self.TW_ETP(self.topic_embeddings, self.word_embeddings)
        # use transport plan as beta
        beta = transp_TW * transp_TW.shape[0]

        return beta

    # only for testing
    def get_theta(self,
                  doc_embeddings,
                  train_doc_embeddings
                ):
        topic_embeddings = self.topic_embeddings.detach().to(doc_embeddings.device)
        dist = pairwise_euclidean_distance(doc_embeddings, topic_embeddings)
        train_dist = pairwise_euclidean_distance(train_doc_embeddings, topic_embeddings)

        exp_dist = torch.exp(-dist / self.theta_temp)
        exp_train_dist = torch.exp(-train_dist / self.theta_temp)

        theta = exp_dist / (exp_train_dist.sum(0))
        theta = theta / theta.sum(1, keepdim=True)

        return theta

    def forward(self, train_bow, doc_embeddings):
        loss_DT, transp_DT = self.DT_ETP(doc_embeddings, self.topic_embeddings)
        loss_TW, transp_TW = self.TW_ETP(self.topic_embeddings, self.word_embeddings)

        loss_ETP = loss_DT + loss_TW

        theta = transp_DT * transp_DT.shape[0]
        beta = transp_TW * transp_TW.shape[0]

        # Dual Semantic-relation Reconstruction
        recon = torch.matmul(theta, beta)

        loss_DSR = -(train_bow * (recon + self.epsilon).log()).sum(axis=1).mean()

        loss = loss_DSR + loss_ETP

        rst_dict = {
            'loss': loss,
        }

        return rst_dict



