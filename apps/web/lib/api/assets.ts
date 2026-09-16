// Typed service functions for the Assets contract. Mirrors
// apps/api/app/assets/router.py exactly: GET (read), and POST creation for
// CreativeBrief / Asset (+ atomic initial AssetVersion) / AssetVersion
// append (MVP-16B). No archive endpoint exists (deferred, MVP-16A §V).

import { request } from "@/lib/api/client";
import type {
  AssetsForContentPieceResponse,
  CreateAssetRequest,
  CreateAssetVersionRequest,
  CreateCreativeBriefRequest,
} from "@/types/assets";

function assetsPath(campaignPublicId: string, contentPublicId: string): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}/content/${encodeURIComponent(contentPublicId)}/assets`;
}

export async function getAssetsForContent(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<AssetsForContentPieceResponse> {
  return request<AssetsForContentPieceResponse>(assetsPath(campaignPublicId, contentPublicId), { method: "GET" });
}

export async function createCreativeBrief(
  campaignPublicId: string,
  contentPublicId: string,
  spec: Record<string, unknown>,
): Promise<AssetsForContentPieceResponse> {
  const body: CreateCreativeBriefRequest = { spec };
  return request<AssetsForContentPieceResponse>(`${assetsPath(campaignPublicId, contentPublicId)}/creative-brief`, {
    method: "POST",
    body,
  });
}

export async function createAsset(
  campaignPublicId: string,
  contentPublicId: string,
  fields: CreateAssetRequest,
): Promise<AssetsForContentPieceResponse> {
  return request<AssetsForContentPieceResponse>(assetsPath(campaignPublicId, contentPublicId), {
    method: "POST",
    body: fields,
  });
}

export async function createAssetVersion(
  campaignPublicId: string,
  contentPublicId: string,
  assetPublicId: string,
  fields: CreateAssetVersionRequest,
): Promise<AssetsForContentPieceResponse> {
  return request<AssetsForContentPieceResponse>(
    `${assetsPath(campaignPublicId, contentPublicId)}/${encodeURIComponent(assetPublicId)}/versions`,
    { method: "POST", body: fields },
  );
}
